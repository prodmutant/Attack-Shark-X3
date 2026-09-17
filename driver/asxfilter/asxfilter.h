/*++

asxfilter.h - internal declarations.

--*/

#ifndef ASXFILTER_H
#define ASXFILTER_H

#include <ntddk.h>
#include <wdf.h>
#include <kbdmou.h>
#include <ntddmou.h>

#include "asxfilter_public.h"

#define ASX_POOL_TAG        'FxsA'      // displays as "AsxF"

//
// mouhid hands us an array of reports; we filter into a stack copy rather than
// editing its buffer. Anything longer is processed in chunks of this size.
//
#define ASX_FILTER_CHUNK    32

//
// If the playback clock falls this far behind, rebase it on now instead of
// firing a catch-up burst.
//
#define ASX_REBASE_100NS    (20 * 1000 * 10LL)      // 20 ms

//
// Per-filter-device context. One instance: the INF binds this driver to the
// X3's devnode only.
//
typedef struct _ASX_DEVICE_CONTEXT {

    WDFDEVICE           Device;

    //
    // What mouclass sent down in IOCTL_INTERNAL_MOUSE_CONNECT: its device
    // object and its MouseClassServiceCallback. Calling this pair is exactly
    // what mouhid does for a physical report.
    //
    CONNECT_DATA        Upper;
    BOOLEAN             Connected;

    //
    // Mirrored from physical reports so emitted ones carry the same unit.
    //
    USHORT              UnitId;

} ASX_DEVICE_CONTEXT, *PASX_DEVICE_CONTEXT;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(ASX_DEVICE_CONTEXT, AsxGetDeviceContext)

//
// Driver-wide state. The control device and the filter device are separate
// WDF objects, so the pieces they share live here.
//
typedef struct _ASX_GLOBALS {

    WDFDEVICE           ControlDevice;
    WDFQUEUE            EventQueue;         // manual, holds pending READ_EVENTS

    //
    // The attached filter instance. Guarded by Lock; NULL when the mouse is
    // unplugged, which is why every user of it re-reads under the lock.
    //
    PASX_DEVICE_CONTEXT Filter;
    KSPIN_LOCK          Lock;

    //
    // Playback queue: a ring of steps plus the high-resolution timer that
    // drains it. NextDue is an absolute system time so a long plan does not
    // accumulate rearm drift.
    //
    PASX_STEP           Ring;
    ULONG               Head;
    ULONG               Tail;
    ULONG               Count;
    BOOLEAN             Playing;
    BOOLEAN             ReleaseOnDrain;
    USHORT              HeldButtons;        // down transitions we emitted
    LONGLONG            NextDue;
    PEX_TIMER           Timer;
    KSPIN_LOCK          PlayLock;

    //
    // Physical-side configuration and the event ring feeding READ_EVENTS.
    //
    ASX_FILTER_CFG      Config;
    PASX_EVENT          Events;
    ULONG               EvHead;
    ULONG               EvTail;
    ULONG               EvCount;
    KSPIN_LOCK          EvLock;

    ULONG64             StepsEmitted;
    ULONG64             PhysicalReports;
    ULONG64             Dropped;

    //
    // Open handles on the control device. A client wants two - one for
    // commands, one parked in a blocking READ_EVENTS - and the safety reset
    // must only fire when the last of them goes, not the first.
    //
    volatile LONG       OpenCount;

} ASX_GLOBALS;

extern ASX_GLOBALS g_Asx;

#define ASX_EVENT_CAPACITY  256

DRIVER_INITIALIZE DriverEntry;

EVT_WDF_DRIVER_DEVICE_ADD            AsxEvtDeviceAdd;
EVT_WDF_IO_QUEUE_IO_INTERNAL_DEVICE_CONTROL AsxEvtInternalDeviceControl;
EVT_WDF_IO_QUEUE_IO_DEVICE_CONTROL   AsxEvtControlDeviceControl;
EVT_WDF_DEVICE_CONTEXT_CLEANUP       AsxEvtDeviceCleanup;
EVT_WDF_FILE_CLEANUP                 AsxEvtFileCleanup;
EVT_WDF_DEVICE_FILE_CREATE           AsxEvtFileCreate;

VOID
AsxServiceCallback(
    _In_ PDEVICE_OBJECT DeviceObject,
    _In_ PMOUSE_INPUT_DATA InputDataStart,
    _In_ PMOUSE_INPUT_DATA InputDataEnd,
    _Inout_ PULONG InputDataConsumed
    );

//
// inject.c
//
NTSTATUS AsxPlaybackInit(VOID);
VOID     AsxPlaybackShutdown(VOID);
NTSTATUS AsxSubmit(_In_reads_(Count) const ASX_STEP *Steps, _In_ ULONG Count, _In_ ULONG Flags);
VOID     AsxStop(_In_ BOOLEAN ReleaseHeld);
VOID     AsxEmit(_In_ USHORT Buttons, _In_ SHORT Data, _In_ LONG Dx, _In_ LONG Dy);

//
// driver.c
//
VOID AsxPushEvent(_In_ const ASX_EVENT *Event);
VOID AsxResetConfig(VOID);

#endif // ASXFILTER_H
