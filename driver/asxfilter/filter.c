/*++

filter.c

    The part that attaches to the mouse. Two jobs:

    1. Intercept IOCTL_INTERNAL_MOUSE_CONNECT on its way down. mouclass sends
       this to claim the device, and the CONNECT_DATA it carries holds the
       address of MouseClassServiceCallback. We keep that pair and substitute
       our own callback before forwarding, which puts us between mouhid and
       mouclass for the rest of the device's life.

    2. Be that callback. Every physical report now arrives here first, so we
       can withhold a bound button from every application in the system
       without a user-mode hook - and, more to the point, we now hold the one
       function pointer that lets us deliver a report of our own that is
       indistinguishable from mouhid's, because it *is* the same call.

--*/

#include "asxfilter.h"

static VOID
AsxForward(
    _In_ WDFREQUEST Request,
    _In_ WDFIOTARGET Target
    )
{
    WDF_REQUEST_SEND_OPTIONS options;

    WdfRequestFormatRequestUsingCurrentType(Request);
    WDF_REQUEST_SEND_OPTIONS_INIT(&options, WDF_REQUEST_SEND_OPTION_SEND_AND_FORGET);

    if (WdfRequestSend(Request, Target, &options) == FALSE) {
        WdfRequestComplete(Request, WdfRequestGetStatus(Request));
    }
}

VOID
AsxEvtInternalDeviceControl(
    _In_ WDFQUEUE Queue,
    _In_ WDFREQUEST Request,
    _In_ size_t OutputBufferLength,
    _In_ size_t InputBufferLength,
    _In_ ULONG IoControlCode
    )
{
    WDFDEVICE           device = WdfIoQueueGetDevice(Queue);
    PASX_DEVICE_CONTEXT ctx = AsxGetDeviceContext(device);
    PCONNECT_DATA       connect = NULL;
    size_t              length = 0;
    NTSTATUS            status;
    KIRQL               irql;

    UNREFERENCED_PARAMETER(OutputBufferLength);
    UNREFERENCED_PARAMETER(InputBufferLength);

    switch (IoControlCode) {

    case IOCTL_INTERNAL_MOUSE_CONNECT:

        //
        // Only one class driver may connect, and we must not be asked twice.
        //
        if (ctx->Upper.ClassService != NULL) {
            WdfRequestComplete(Request, STATUS_SHARING_VIOLATION);
            return;
        }

        status = WdfRequestRetrieveInputBuffer(Request,
                                               sizeof(CONNECT_DATA),
                                               (PVOID *)&connect,
                                               &length);
        if (!NT_SUCCESS(status) || length < sizeof(CONNECT_DATA)) {
            WdfRequestComplete(Request, NT_SUCCESS(status) ? STATUS_BUFFER_TOO_SMALL : status);
            return;
        }

        //
        // Keep mouclass's real pair, then hand mouhid ours instead.
        //
        ctx->Upper = *connect;

        connect->ClassDeviceObject = WdfDeviceWdmGetDeviceObject(device);
        connect->ClassService = (PVOID)AsxServiceCallback;

        ctx->Connected = TRUE;

        KeAcquireSpinLock(&g_Asx.Lock, &irql);
        g_Asx.Filter = ctx;
        KeReleaseSpinLock(&g_Asx.Lock, irql);

        break;

    case IOCTL_INTERNAL_MOUSE_DISCONNECT:

        //
        // mouclass is letting go. Stop anything in flight before the callback
        // we hold becomes invalid, and forget the instance.
        //
        AsxStop(FALSE);

        KeAcquireSpinLock(&g_Asx.Lock, &irql);
        if (g_Asx.Filter == ctx) {
            g_Asx.Filter = NULL;
        }
        KeReleaseSpinLock(&g_Asx.Lock, irql);

        ctx->Connected = FALSE;
        RtlZeroMemory(&ctx->Upper, sizeof(ctx->Upper));
        break;

    default:
        break;
    }

    AsxForward(Request, WdfDeviceGetIoTarget(device));
}

/*++

AsxServiceCallback

    Called by mouhid at DISPATCH_LEVEL for every physical report, with an
    array of MOUSE_INPUT_DATA. We copy into a stack buffer rather than editing
    mouhid's, apply the suppression config, and pass whatever survives up to
    the real mouclass callback.

    On InputDataConsumed: when we withhold entries, mouclass is told about
    fewer reports than mouhid handed us, so we report the original count back
    to mouhid ourselves. Otherwise mouhid would believe the tail was not taken.

--*/
VOID
AsxServiceCallback(
    _In_ PDEVICE_OBJECT DeviceObject,
    _In_ PMOUSE_INPUT_DATA InputDataStart,
    _In_ PMOUSE_INPUT_DATA InputDataEnd,
    _Inout_ PULONG InputDataConsumed
    )
{
    WDFDEVICE           device = WdfWdmDeviceGetWdfDeviceHandle(DeviceObject);
    PASX_DEVICE_CONTEXT ctx = AsxGetDeviceContext(device);
    MOUSE_INPUT_DATA    keep[ASX_FILTER_CHUNK];
    ASX_FILTER_CFG      cfg;
    PMOUSE_INPUT_DATA   in;
    ULONG               total;
    ULONG               consumed;
    ULONG               kept;
    ULONG               i;

    total = (ULONG)(InputDataEnd - InputDataStart);
    if (total == 0 || ctx == NULL || ctx->Upper.ClassService == NULL) {
        *InputDataConsumed = total;
        return;
    }

    g_Asx.PhysicalReports += total;

    //
    // A torn read of the config is harmless: every field is a single ULONG and
    // the worst case is that one report obeys the previous setting.
    //
    cfg = g_Asx.Config;

    if (cfg.SuppressButtons == 0 && cfg.SuppressMove == 0 && cfg.ReportEvents == 0) {
        ctx->UnitId = InputDataStart->UnitId;
        ((PSERVICE_CALLBACK_ROUTINE)ctx->Upper.ClassService)(
            ctx->Upper.ClassDeviceObject, InputDataStart, InputDataEnd, InputDataConsumed);
        return;
    }

    consumed = 0;
    in = InputDataStart;

    while (in < InputDataEnd) {

        ULONG chunk = (ULONG)(InputDataEnd - in);
        if (chunk > ASX_FILTER_CHUNK) {
            chunk = ASX_FILTER_CHUNK;
        }

        kept = 0;

        for (i = 0; i < chunk; i++) {

            MOUSE_INPUT_DATA d = in[i];
            USHORT  original = d.ButtonFlags;
            BOOLEAN dropped;

            ctx->UnitId = d.UnitId;

            if (cfg.SuppressButtons != 0) {
                d.ButtonFlags &= (USHORT)~(cfg.SuppressButtons & 0xFFFFU);
                if ((original & (ASX_WHEEL | ASX_HWHEEL)) != 0 &&
                    (d.ButtonFlags & (ASX_WHEEL | ASX_HWHEEL)) == 0) {
                    d.ButtonData = 0;
                }
            }
            if (cfg.SuppressMove != 0) {
                d.LastX = 0;
                d.LastY = 0;
            }

            dropped = (BOOLEAN)(d.ButtonFlags == 0 && d.LastX == 0 && d.LastY == 0);

            if (cfg.ReportEvents != 0) {
                ASX_EVENT ev;
                ev.Time = (ULONG64)KeQueryInterruptTime();
                ev.Buttons = original;
                ev.Data = (SHORT)in[i].ButtonData;
                ev.Dx = in[i].LastX;
                ev.Dy = in[i].LastY;
                ev.Suppressed = dropped ? 1UL : 0UL;
                AsxPushEvent(&ev);
            }

            if (!dropped) {
                keep[kept++] = d;
            }
        }

        if (kept != 0) {
            ULONG taken = 0;
            ((PSERVICE_CALLBACK_ROUTINE)ctx->Upper.ClassService)(
                ctx->Upper.ClassDeviceObject, keep, keep + kept, &taken);
        }

        consumed += chunk;
        in += chunk;
    }

    *InputDataConsumed = consumed;
}
