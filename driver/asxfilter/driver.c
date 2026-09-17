/*++

driver.c

    Entry point, the filter devnode, and the control device user mode talks to.

    Two device objects with different lifetimes: the filter device comes and
    goes with the mouse (the X3 is wireless, so it goes whenever the dongle
    sleeps), while the control device exists for as long as the driver is
    loaded. Anything they share lives in g_Asx.

    The control device is restricted to SYSTEM and Administrators. A handle to
    it can move the pointer and withhold clicks from every application on the
    desktop, which is not something an unprivileged process should be able to
    ask for.

--*/

#include "asxfilter.h"

//
// D:P                  discretionary ACL, protected from inheritance
// (A;;GA;;;SY)         allow generic-all to SYSTEM
// (A;;GA;;;BA)         allow generic-all to the built-in Administrators group
//
DECLARE_CONST_UNICODE_STRING(AsxControlSddl, L"D:P(A;;GA;;;SY)(A;;GA;;;BA)");
DECLARE_CONST_UNICODE_STRING(AsxNtName, ASX_NT_DEVICE_NAME);
DECLARE_CONST_UNICODE_STRING(AsxSymName, ASX_SYMBOLIC_NAME);

static EVT_WDF_DRIVER_UNLOAD AsxEvtDriverUnload;

static NTSTATUS AsxCreateControlDevice(_In_ WDFDRIVER Driver);

NTSTATUS
DriverEntry(
    _In_ PDRIVER_OBJECT DriverObject,
    _In_ PUNICODE_STRING RegistryPath
    )
{
    WDF_DRIVER_CONFIG config;
    WDFDRIVER         driver;
    NTSTATUS          status;

    WDF_DRIVER_CONFIG_INIT(&config, AsxEvtDeviceAdd);
    config.EvtDriverUnload = AsxEvtDriverUnload;

    status = WdfDriverCreate(DriverObject, RegistryPath,
                             WDF_NO_OBJECT_ATTRIBUTES, &config, &driver);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    status = AsxPlaybackInit();
    if (!NT_SUCCESS(status)) {
        return status;
    }

    status = AsxCreateControlDevice(driver);
    if (!NT_SUCCESS(status)) {
        AsxPlaybackShutdown();
        return status;
    }

    return STATUS_SUCCESS;
}

static VOID
AsxEvtDriverUnload(
    _In_ WDFDRIVER Driver
    )
{
    UNREFERENCED_PARAMETER(Driver);
    AsxPlaybackShutdown();
}

/*++

AsxEvtDeviceAdd

    Called once for the X3's mouse devnode - the INF binds this driver to that
    hardware ID only, so it never lands on another pointing device. We attach
    as a filter and take internal device control; KMDF forwards every other
    request type down the stack by itself because of WdfFdoInitSetFilter.

--*/
NTSTATUS
AsxEvtDeviceAdd(
    _In_ WDFDRIVER Driver,
    _Inout_ PWDFDEVICE_INIT DeviceInit
    )
{
    WDF_OBJECT_ATTRIBUTES attributes;
    WDF_IO_QUEUE_CONFIG   queueConfig;
    WDFDEVICE             device;
    PASX_DEVICE_CONTEXT   ctx;
    NTSTATUS              status;

    UNREFERENCED_PARAMETER(Driver);

    WdfFdoInitSetFilter(DeviceInit);

    WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(&attributes, ASX_DEVICE_CONTEXT);
    attributes.EvtCleanupCallback = AsxEvtDeviceCleanup;

    status = WdfDeviceCreate(&DeviceInit, &attributes, &device);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    ctx = AsxGetDeviceContext(device);
    RtlZeroMemory(ctx, sizeof(*ctx));
    ctx->Device = device;

    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(&queueConfig, WdfIoQueueDispatchParallel);
    queueConfig.EvtIoInternalDeviceControl = AsxEvtInternalDeviceControl;

    status = WdfIoQueueCreate(device, &queueConfig, WDF_NO_OBJECT_ATTRIBUTES, WDF_NO_HANDLE);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    return STATUS_SUCCESS;
}

VOID
AsxEvtDeviceCleanup(
    _In_ WDFOBJECT Object
    )
{
    PASX_DEVICE_CONTEXT ctx = AsxGetDeviceContext((WDFDEVICE)Object);
    KIRQL irql;

    //
    // The mouse is going away and the callback we captured goes with it.
    // Cancel playback before the pointer can become stale.
    //
    AsxStop(FALSE);

    KeAcquireSpinLock(&g_Asx.Lock, &irql);
    if (g_Asx.Filter == ctx) {
        g_Asx.Filter = NULL;
    }
    KeReleaseSpinLock(&g_Asx.Lock, irql);
}

// ------------------------------------------------------------- events ----

static BOOLEAN
AsxTryFillRequest(
    _In_ WDFREQUEST Request
    )
{
    PASX_EVENT out = NULL;
    size_t     len = 0;
    NTSTATUS   status;
    KIRQL      irql;
    ULONG      cap;
    ULONG      n = 0;

    status = WdfRequestRetrieveOutputBuffer(Request, sizeof(ASX_EVENT),
                                            (PVOID *)&out, &len);
    if (!NT_SUCCESS(status)) {
        WdfRequestComplete(Request, status);
        return TRUE;
    }

    cap = (ULONG)(len / sizeof(ASX_EVENT));

    KeAcquireSpinLock(&g_Asx.EvLock, &irql);
    while (n < cap && g_Asx.EvCount != 0) {
        out[n++] = g_Asx.Events[g_Asx.EvHead];
        g_Asx.EvHead = (g_Asx.EvHead + 1) % ASX_EVENT_CAPACITY;
        g_Asx.EvCount--;
    }
    KeReleaseSpinLock(&g_Asx.EvLock, irql);

    if (n == 0) {
        return FALSE;
    }

    WdfRequestCompleteWithInformation(Request, STATUS_SUCCESS,
                                      (ULONG_PTR)n * sizeof(ASX_EVENT));
    return TRUE;
}

/*++

AsxPushEvent

    Called from the service callback at DISPATCH_LEVEL for each physical
    report, when the daemon has asked to see them. A waiting READ_EVENTS is
    completed immediately, so a macro trigger costs no polling interval - the
    notification leaves the kernel before mouclass has even seen the click.

--*/
VOID
AsxPushEvent(
    _In_ const ASX_EVENT *Event
    )
{
    WDFREQUEST request;
    KIRQL      irql;

    if (g_Asx.Events == NULL) {
        return;
    }

    KeAcquireSpinLock(&g_Asx.EvLock, &irql);
    if (g_Asx.EvCount == ASX_EVENT_CAPACITY) {
        g_Asx.EvHead = (g_Asx.EvHead + 1) % ASX_EVENT_CAPACITY;   // drop oldest
        g_Asx.EvCount--;
    }
    g_Asx.Events[g_Asx.EvTail] = *Event;
    g_Asx.EvTail = (g_Asx.EvTail + 1) % ASX_EVENT_CAPACITY;
    g_Asx.EvCount++;
    KeReleaseSpinLock(&g_Asx.EvLock, irql);

    if (g_Asx.EventQueue == NULL) {
        return;
    }
    if (NT_SUCCESS(WdfIoQueueRetrieveNextRequest(g_Asx.EventQueue, &request))) {
        if (!AsxTryFillRequest(request)) {
            (VOID)WdfRequestForwardToIoQueue(request, g_Asx.EventQueue);
        }
    }
}

VOID
AsxResetConfig(VOID)
{
    g_Asx.Config.SuppressButtons = 0;
    g_Asx.Config.SuppressMove = 0;
    g_Asx.Config.ReportEvents = 0;
}

// ----------------------------------------------------- control device ----

VOID
AsxEvtFileCreate(
    _In_ WDFDEVICE Device,
    _In_ WDFREQUEST Request,
    _In_ WDFFILEOBJECT FileObject
    )
{
    UNREFERENCED_PARAMETER(Device);
    UNREFERENCED_PARAMETER(FileObject);

    InterlockedIncrement(&g_Asx.OpenCount);
    WdfRequestComplete(Request, STATUS_SUCCESS);
}

VOID
AsxEvtFileCleanup(
    _In_ WDFFILEOBJECT FileObject
    )
{
    UNREFERENCED_PARAMETER(FileObject);

    //
    // A client holds more than one handle, so only the last one leaving means
    // the daemon has gone - whether it meant to or not. Everything it changed
    // about the physical mouse is undone here, so a crash can never leave a
    // button swallowed or a movement plan running with nobody to stop it.
    //
    if (InterlockedDecrement(&g_Asx.OpenCount) > 0) {
        return;
    }

    AsxStop(TRUE);
    AsxResetConfig();

    if (g_Asx.EventQueue != NULL) {
        WdfIoQueuePurgeSynchronously(g_Asx.EventQueue);
        WdfIoQueueStart(g_Asx.EventQueue);
    }
}

VOID
AsxEvtControlDeviceControl(
    _In_ WDFQUEUE Queue,
    _In_ WDFREQUEST Request,
    _In_ size_t OutputBufferLength,
    _In_ size_t InputBufferLength,
    _In_ ULONG IoControlCode
    )
{
    NTSTATUS   status = STATUS_INVALID_DEVICE_REQUEST;
    ULONG_PTR  info = 0;
    PVOID      in = NULL;
    PVOID      out = NULL;
    size_t     len = 0;
    KIRQL      irql;

    UNREFERENCED_PARAMETER(Queue);

    switch (IoControlCode) {

    case IOCTL_ASX_STATUS: {

        ASX_STATUS *st;

        if (OutputBufferLength < sizeof(ASX_STATUS)) {
            status = STATUS_BUFFER_TOO_SMALL;
            break;
        }
        status = WdfRequestRetrieveOutputBuffer(Request, sizeof(ASX_STATUS), &out, &len);
        if (!NT_SUCCESS(status)) {
            break;
        }
        st = (ASX_STATUS *)out;
        RtlZeroMemory(st, sizeof(*st));
        st->Version = ASX_INTERFACE_VERSION;

        KeAcquireSpinLock(&g_Asx.Lock, &irql);
        st->Attached = (g_Asx.Filter != NULL) ? 1UL : 0UL;
        st->Connected = (g_Asx.Filter != NULL && g_Asx.Filter->Connected) ? 1UL : 0UL;
        KeReleaseSpinLock(&g_Asx.Lock, irql);

        KeAcquireSpinLock(&g_Asx.PlayLock, &irql);
        st->Playing = g_Asx.Playing ? 1UL : 0UL;
        st->Queued = g_Asx.Count;
        KeReleaseSpinLock(&g_Asx.PlayLock, irql);

        st->Capacity = ASX_QUEUE_CAPACITY;
        st->SuppressButtons = g_Asx.Config.SuppressButtons;
        st->SuppressMove = g_Asx.Config.SuppressMove;
        st->StepsEmitted = g_Asx.StepsEmitted;
        st->PhysicalReports = g_Asx.PhysicalReports;
        st->Dropped = g_Asx.Dropped;

        info = sizeof(ASX_STATUS);
        status = STATUS_SUCCESS;
        break;
    }

    case IOCTL_ASX_SUBMIT: {

        ASX_SUBMIT *sub;
        size_t      need;

        if (InputBufferLength < sizeof(ASX_SUBMIT)) {
            status = STATUS_BUFFER_TOO_SMALL;
            break;
        }
        status = WdfRequestRetrieveInputBuffer(Request, sizeof(ASX_SUBMIT), &in, &len);
        if (!NT_SUCCESS(status)) {
            break;
        }
        sub = (ASX_SUBMIT *)in;

        if (sub->Count == 0 || sub->Count > ASX_MAX_STEPS_PER_SUBMIT) {
            status = STATUS_INVALID_PARAMETER;
            break;
        }
        //
        // The count is attacker-controlled even from an administrator, so the
        // buffer must be proved big enough before a single step is read.
        //
        need = FIELD_OFFSET(ASX_SUBMIT, Steps) + (size_t)sub->Count * sizeof(ASX_STEP);
        if (len < need) {
            status = STATUS_BUFFER_TOO_SMALL;
            break;
        }

        status = AsxSubmit(sub->Steps, sub->Count, sub->Flags);
        break;
    }

    case IOCTL_ASX_STOP:
        AsxStop(TRUE);
        status = STATUS_SUCCESS;
        break;

    case IOCTL_ASX_SET_FILTER: {

        ASX_FILTER_CFG *cfg;

        if (InputBufferLength < sizeof(ASX_FILTER_CFG)) {
            status = STATUS_BUFFER_TOO_SMALL;
            break;
        }
        status = WdfRequestRetrieveInputBuffer(Request, sizeof(ASX_FILTER_CFG), &in, &len);
        if (!NT_SUCCESS(status)) {
            break;
        }
        cfg = (ASX_FILTER_CFG *)in;

        g_Asx.Config.SuppressButtons = cfg->SuppressButtons & ASX_ALL_BUTTONS;
        g_Asx.Config.SuppressMove = cfg->SuppressMove;
        g_Asx.Config.ReportEvents = cfg->ReportEvents;

        status = STATUS_SUCCESS;
        break;
    }

    case IOCTL_ASX_READ_EVENTS: {

        if (OutputBufferLength < sizeof(ASX_EVENT)) {
            status = STATUS_BUFFER_TOO_SMALL;
            break;
        }
        if (AsxTryFillRequest(Request)) {
            return;                     // already completed
        }
        status = WdfRequestForwardToIoQueue(Request, g_Asx.EventQueue);
        if (!NT_SUCCESS(status)) {
            break;
        }
        //
        // An event may have arrived between the drain and the forward, and
        // found no waiting request. Look again so it cannot sit unread.
        //
        KeAcquireSpinLock(&g_Asx.EvLock, &irql);
        len = g_Asx.EvCount;
        KeReleaseSpinLock(&g_Asx.EvLock, irql);

        if (len != 0) {
            WDFREQUEST pending;
            if (NT_SUCCESS(WdfIoQueueRetrieveNextRequest(g_Asx.EventQueue, &pending))) {
                if (!AsxTryFillRequest(pending)) {
                    (VOID)WdfRequestForwardToIoQueue(pending, g_Asx.EventQueue);
                }
            }
        }
        return;
    }

    default:
        break;
    }

    WdfRequestCompleteWithInformation(Request, status, info);
}

static NTSTATUS
AsxCreateControlDevice(
    _In_ WDFDRIVER Driver
    )
{
    PWDFDEVICE_INIT         init;
    WDF_OBJECT_ATTRIBUTES   attributes;
    WDF_FILEOBJECT_CONFIG   fileConfig;
    WDF_IO_QUEUE_CONFIG     queueConfig;
    WDFDEVICE               control;
    NTSTATUS                status;

    init = WdfControlDeviceInitAllocate(Driver, &AsxControlSddl);
    if (init == NULL) {
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    WdfDeviceInitSetDeviceType(init, FILE_DEVICE_UNKNOWN);
    WdfDeviceInitSetCharacteristics(init, FILE_DEVICE_SECURE_OPEN, FALSE);
    WdfDeviceInitSetExclusive(init, FALSE);

    status = WdfDeviceInitAssignName(init, &AsxNtName);
    if (!NT_SUCCESS(status)) {
        goto fail;
    }

    WDF_FILEOBJECT_CONFIG_INIT(&fileConfig, AsxEvtFileCreate,
                               WDF_NO_EVENT_CALLBACK, AsxEvtFileCleanup);
    WdfDeviceInitSetFileObjectConfig(init, &fileConfig, WDF_NO_OBJECT_ATTRIBUTES);

    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);

    status = WdfDeviceCreate(&init, &attributes, &control);
    if (!NT_SUCCESS(status)) {
        goto fail;
    }

    status = WdfDeviceCreateSymbolicLink(control, &AsxSymName);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(&queueConfig, WdfIoQueueDispatchParallel);
    queueConfig.EvtIoDeviceControl = AsxEvtControlDeviceControl;

    status = WdfIoQueueCreate(control, &queueConfig, WDF_NO_OBJECT_ATTRIBUTES, WDF_NO_HANDLE);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    //
    // Manual queue: READ_EVENTS parks here until a physical report arrives.
    //
    WDF_IO_QUEUE_CONFIG_INIT(&queueConfig, WdfIoQueueDispatchManual);
    status = WdfIoQueueCreate(control, &queueConfig, WDF_NO_OBJECT_ATTRIBUTES,
                              &g_Asx.EventQueue);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    g_Asx.ControlDevice = control;

    WdfControlFinishInitializing(control);
    return STATUS_SUCCESS;

fail:
    WdfDeviceInitFree(init);
    return status;
}
