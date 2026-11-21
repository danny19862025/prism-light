// SPDX-License-Identifier: GPL-3.0-only
// Copyright (C) 2026 Prism Light contributors
//
// Prism Monitor frame timing layer for Linux: the counterpart of PresentMon on Windows.
//
// An implicit Vulkan layer that notes the time of every vkQueuePresentKHR and sends
// one small datagram per present to Prism Monitor's socket in the abstract namespace
// ("\0prism-monitor-fps-<uid>"). It never draws, never changes or delays a frame and
// never blocks: if Prism Monitor is not running the send fails and the layer backs
// off for a second. The receiver checks the sender's uid and pid from the kernel
// (SO_PASSCRED), so the message carries only the swapchain, timestamp and name.
//
// Datagram: "PFPS1\t<swapchain>\t<CLOCK_MONOTONIC ns>\t<process name>"
// Opt out for one game with DISABLE_PRISM_FPS=1.
#define _GNU_SOURCE
#include <pthread.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include <vulkan/vulkan.h>
#include <vulkan/vk_layer.h>

#define LAYER_NAME "VK_LAYER_PRISM_monitor_fps"
#define EXPORT __attribute__((visibility("default")))
#define MAX_OBJECTS 256

typedef struct {
    void *key;
    PFN_vkGetInstanceProcAddr gipa;
    PFN_vkDestroyInstance destroy;
} InstanceEntry;

typedef struct {
    void *key;
    PFN_vkGetDeviceProcAddr gdpa;
    PFN_vkDestroyDevice destroy;
    PFN_vkQueuePresentKHR present;
} DeviceEntry;

static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static InstanceEntry instances[MAX_OBJECTS];
static DeviceEntry devices[MAX_OBJECTS];

static void *key_of(const void *handle) { return handle ? *(void *const *)handle : NULL; }

static InstanceEntry *find_instance(void *key) {
    for (int i = 0; i < MAX_OBJECTS; i++)
        if (instances[i].key == key) return &instances[i];
    return NULL;
}

static DeviceEntry *find_device(void *key) {
    for (int i = 0; i < MAX_OBJECTS; i++)
        if (devices[i].key == key) return &devices[i];
    return NULL;
}

// -- sending -------------------------------------------------------------------
static int sock = -1;
static struct sockaddr_un addr;
static socklen_t addr_len;
static long long retry_at;     // CLOCK_MONOTONIC ns before which sending is skipped
static char name[128];

static long long now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

static void read_name(void) {
    // argv[0] is the game's .exe under Proton/Wine and the program path otherwise.
    char buf[4096] = {0};
    FILE *f = fopen("/proc/self/cmdline", "rb");
    size_t n = f ? fread(buf, 1, sizeof buf - 1, f) : 0;
    if (f) fclose(f);
    buf[n < sizeof buf ? n : sizeof buf - 1] = 0;
    const char *base = buf;
    for (const char *p = buf; *p; p++)
        if (*p == '/' || *p == '\\') base = p + 1;
    if (!*base || !strncmp(base, "wine", 4)) {
        f = fopen("/proc/self/comm", "r");
        if (f) {
            if (!fgets(buf, sizeof buf, f)) buf[0] = 0;
            fclose(f);
            buf[strcspn(buf, "\n")] = 0;
            base = buf;
        }
    }
    snprintf(name, sizeof name, "%s", base);
    for (char *p = name; *p; p++)
        if (*p == '\t' || *p == '\n') *p = ' ';
}

static void open_socket(void) {
    sock = socket(AF_UNIX, SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    int len = snprintf(addr.sun_path + 1, sizeof addr.sun_path - 1, "prism-monitor-fps-%u", (unsigned)getuid());
    addr_len = (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + len);
    read_name();
}

static void note_present(const VkPresentInfoKHR *info) {
    if (!info || sock < 0) return;
    long long t = now_ns();
    if (t < retry_at) return;
    for (uint32_t i = 0; i < info->swapchainCount; i++) {
        char msg[256];
        int len = snprintf(msg, sizeof msg, "PFPS1\t%llx\t%lld\t%s",
                           (unsigned long long)(uintptr_t)info->pSwapchains[i], t, name);
        if (sendto(sock, msg, (size_t)len, MSG_DONTWAIT | MSG_NOSIGNAL, (struct sockaddr *)&addr, addr_len) < 0) {
            retry_at = t + 1000000000LL;   // nobody listening (or the queue is full): try again in a second
            return;
        }
    }
}

// -- intercepted calls -----------------------------------------------------------
static VKAPI_ATTR VkResult VKAPI_CALL layer_QueuePresentKHR(VkQueue queue, const VkPresentInfoKHR *info) {
    pthread_mutex_lock(&lock);
    DeviceEntry *d = find_device(key_of(queue));
    PFN_vkQueuePresentKHR next = d ? d->present : NULL;
    pthread_mutex_unlock(&lock);
    if (!next) return VK_ERROR_DEVICE_LOST;
    note_present(info);
    return next(queue, info);
}

static VKAPI_ATTR VkResult VKAPI_CALL layer_CreateInstance(const VkInstanceCreateInfo *info,
                                                           const VkAllocationCallbacks *alloc, VkInstance *out) {
    VkLayerInstanceCreateInfo *link = (VkLayerInstanceCreateInfo *)info->pNext;
    while (link && !(link->sType == VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO && link->function == VK_LAYER_LINK_INFO))
        link = (VkLayerInstanceCreateInfo *)link->pNext;
    if (!link) return VK_ERROR_INITIALIZATION_FAILED;
    PFN_vkGetInstanceProcAddr gipa = link->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    link->u.pLayerInfo = link->u.pLayerInfo->pNext;
    PFN_vkCreateInstance create = (PFN_vkCreateInstance)gipa(VK_NULL_HANDLE, "vkCreateInstance");
    if (!create) return VK_ERROR_INITIALIZATION_FAILED;
    VkResult result = create(info, alloc, out);
    if (result != VK_SUCCESS) return result;
    pthread_mutex_lock(&lock);
    InstanceEntry *e = find_instance(NULL);
    if (e) {
        e->key = key_of(*out);
        e->gipa = gipa;
        e->destroy = (PFN_vkDestroyInstance)gipa(*out, "vkDestroyInstance");
    }
    if (sock < 0) open_socket();
    pthread_mutex_unlock(&lock);
    return VK_SUCCESS;
}

static VKAPI_ATTR void VKAPI_CALL layer_DestroyInstance(VkInstance instance, const VkAllocationCallbacks *alloc) {
    pthread_mutex_lock(&lock);
    InstanceEntry *e = find_instance(key_of(instance));
    PFN_vkDestroyInstance destroy = e ? e->destroy : NULL;
    if (e) memset(e, 0, sizeof *e);
    pthread_mutex_unlock(&lock);
    if (destroy) destroy(instance, alloc);
}

static VKAPI_ATTR VkResult VKAPI_CALL layer_CreateDevice(VkPhysicalDevice gpu, const VkDeviceCreateInfo *info,
                                                         const VkAllocationCallbacks *alloc, VkDevice *out) {
    VkLayerDeviceCreateInfo *link = (VkLayerDeviceCreateInfo *)info->pNext;
    while (link && !(link->sType == VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO && link->function == VK_LAYER_LINK_INFO))
        link = (VkLayerDeviceCreateInfo *)link->pNext;
    if (!link) return VK_ERROR_INITIALIZATION_FAILED;
    PFN_vkGetInstanceProcAddr gipa = link->u.pLayerInfo->pfnNextGetInstanceProcAddr;
    PFN_vkGetDeviceProcAddr gdpa = link->u.pLayerInfo->pfnNextGetDeviceProcAddr;
    link->u.pLayerInfo = link->u.pLayerInfo->pNext;
    PFN_vkCreateDevice create = (PFN_vkCreateDevice)gipa(VK_NULL_HANDLE, "vkCreateDevice");
    if (!create) return VK_ERROR_INITIALIZATION_FAILED;
    VkResult result = create(gpu, info, alloc, out);
    if (result != VK_SUCCESS) return result;
    pthread_mutex_lock(&lock);
    DeviceEntry *d = find_device(NULL);
    if (d) {
        d->key = key_of(*out);
        d->gdpa = gdpa;
        d->destroy = (PFN_vkDestroyDevice)gdpa(*out, "vkDestroyDevice");
        d->present = (PFN_vkQueuePresentKHR)gdpa(*out, "vkQueuePresentKHR");
    }
    pthread_mutex_unlock(&lock);
    return VK_SUCCESS;
}

static VKAPI_ATTR void VKAPI_CALL layer_DestroyDevice(VkDevice device, const VkAllocationCallbacks *alloc) {
    pthread_mutex_lock(&lock);
    DeviceEntry *d = find_device(key_of(device));
    PFN_vkDestroyDevice destroy = d ? d->destroy : NULL;
    if (d) memset(d, 0, sizeof *d);
    pthread_mutex_unlock(&lock);
    if (destroy) destroy(device, alloc);
}

// -- dispatch ------------------------------------------------------------------
EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL prism_GetDeviceProcAddr(VkDevice device, const char *fn);

static PFN_vkVoidFunction own_device_function(const char *fn) {
    if (!strcmp(fn, "vkGetDeviceProcAddr")) return (PFN_vkVoidFunction)prism_GetDeviceProcAddr;
    if (!strcmp(fn, "vkDestroyDevice")) return (PFN_vkVoidFunction)layer_DestroyDevice;
    if (!strcmp(fn, "vkQueuePresentKHR")) return (PFN_vkVoidFunction)layer_QueuePresentKHR;
    return NULL;
}

EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL prism_GetDeviceProcAddr(VkDevice device, const char *fn) {
    PFN_vkVoidFunction own = own_device_function(fn);
    if (own) return own;
    pthread_mutex_lock(&lock);
    DeviceEntry *d = find_device(key_of(device));
    PFN_vkGetDeviceProcAddr next = d ? d->gdpa : NULL;
    pthread_mutex_unlock(&lock);
    return next ? next(device, fn) : NULL;
}

EXPORT VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL prism_GetInstanceProcAddr(VkInstance instance, const char *fn) {
    if (!strcmp(fn, "vkGetInstanceProcAddr")) return (PFN_vkVoidFunction)prism_GetInstanceProcAddr;
    if (!strcmp(fn, "vkCreateInstance")) return (PFN_vkVoidFunction)layer_CreateInstance;
    if (!strcmp(fn, "vkDestroyInstance")) return (PFN_vkVoidFunction)layer_DestroyInstance;
    if (!strcmp(fn, "vkCreateDevice")) return (PFN_vkVoidFunction)layer_CreateDevice;
    PFN_vkVoidFunction own = own_device_function(fn);
    if (own) return own;
    pthread_mutex_lock(&lock);
    InstanceEntry *e = find_instance(key_of(instance));
    PFN_vkGetInstanceProcAddr next = e ? e->gipa : NULL;
    pthread_mutex_unlock(&lock);
    return next ? next(instance, fn) : NULL;
}

EXPORT VKAPI_ATTR VkResult VKAPI_CALL vkNegotiateLoaderLayerInterfaceVersion(VkNegotiateLayerInterface *v) {
    if (!v || v->sType != LAYER_NEGOTIATE_INTERFACE_STRUCT) return VK_ERROR_INITIALIZATION_FAILED;
    if (v->loaderLayerInterfaceVersion < 2) return VK_ERROR_INITIALIZATION_FAILED;
    v->pfnGetInstanceProcAddr = prism_GetInstanceProcAddr;
    v->pfnGetDeviceProcAddr = prism_GetDeviceProcAddr;
    v->pfnGetPhysicalDeviceProcAddr = NULL;
    v->loaderLayerInterfaceVersion = 2;
    return VK_SUCCESS;
}
