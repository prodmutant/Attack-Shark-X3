/*++

inject.c

    Playback. A submitted plan is a whole trajectory - typically one report per
    millisecond for the length of the movement - so it is handed over in a
    single IOCTL and clocked out here rather than being driven step by step
    from user mode. That matters twice over: the timing comes from a kernel
    high-resolution timer instead of a user-mode sleep, and a scheduling hiccup
    in the daemon cannot stretch the middle of a movement.

    The clock is scheduled against an absolute deadline that advances by each
    step's delay, so a 4000-step plan lands where it should instead of
    accumulating one rearm's worth of lateness per step.

    Emission itself is AsxEmit: fill in a MOUSE_INPUT_DATA and call the
    mouclass service callback captured in filter.c. That is the same function,
    with the same arguments, at the same IRQL, that mouhid calls when you move
    the mouse by hand.

--*/

#include "asxfilter.h"

ASX_GLOBALS g_Asx = { 0 };

//
// down transition -> the up transition that clears it is always (down << 1)
//
static const USHORT AsxDownBits[] = {
    ASX_LEFT_DOWN, ASX_RIGHT_DOWN, ASX_MIDDLE_DOWN, ASX_BUTTON4_DOWN, ASX_BUTTON5_DOWN
};

static VOID AsxReleaseHeld(VOID);

static EXT_CALLBACK AsxTimerCallback;

NTSTATUS
AsxPlaybackInit(VOID)
{
    KeInitializeSpinLock(&g_Asx.Lock);
    KeInitializeSpinLock(&g_Asx.PlayLock);
    KeInitializeSpinLock(&g_Asx.EvLock);

    g_Asx.Ring = (PASX_STEP)ExAllocatePool2(
        POOL_FLAG_NON_PAGED, (SIZE_T)ASX_QUEUE_CAPACITY * sizeof(ASX_STEP), ASX_POOL_TAG);
    if (g_Asx.Ring == NULL) {
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    g_Asx.Events = (PASX_EVENT)ExAllocatePool2(
        POOL_FLAG_NON_PAGED, (SIZE_T)ASX_EVENT_CAPACITY * sizeof(ASX_EVENT), ASX_POOL_TAG);
    if (g_Asx.Events == NULL) {
        ExFreePoolWithTag(g_Asx.Ring, ASX_POOL_TAG);
        g_Asx.Ring = NULL;
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    //
    // EX_TIMER_HIGH_RESOLUTION is what makes a 1 ms cadence meaningful; a
    // default timer would quantise every step to the 15.6 ms system tick.
    //
    g_Asx.Timer = ExAllocateTimer(AsxTimerCallback, NULL, EX_TIMER_HIGH_RESOLUTION);
    if (g_Asx.Timer == NULL) {
        ExFreePoolWithTag(g_Asx.Events, ASX_POOL_TAG);
        ExFreePoolWithTag(g_Asx.Ring, ASX_POOL_TAG);
        g_Asx.Events = NULL;
        g_Asx.Ring = NULL;
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    return STATUS_SUCCESS;
}

VOID
AsxPlaybackShutdown(VOID)
{
    if (g_Asx.Timer != NULL) {
        ExDeleteTimer(g_Asx.Timer, TRUE, TRUE, NULL);   // cancel, and wait for it
        g_Asx.Timer = NULL;
    }
    if (g_Asx.Ring != NULL) {
        ExFreePoolWithTag(g_Asx.Ring, ASX_POOL_TAG);
        g_Asx.Ring = NULL;
    }
    if (g_Asx.Events != NULL) {
        ExFreePoolWithTag(g_Asx.Events, ASX_POOL_TAG);
        g_Asx.Events = NULL;
    }
}

/*++

AsxEmit

    One report, delivered through mouclass's own entry point. Callable from
    either IRQL: the class callback contract is DISPATCH_LEVEL, so raise if we
    arrived here from an IOCTL.

--*/
VOID
AsxEmit(
    _In_ USHORT Buttons,
    _In_ SHORT Data,
    _In_ LONG Dx,
    _In_ LONG Dy
    )
{
    PASX_DEVICE_CONTEXT ctx;
    MOUSE_INPUT_DATA    d;
    ULONG               taken = 0;
    KIRQL               old = PASSIVE_LEVEL;
    BOOLEAN             raised = FALSE;
    ULONG               i;

    if (Dx > ASX_MAX_DELTA)  { Dx = ASX_MAX_DELTA; }
    if (Dx < -ASX_MAX_DELTA) { Dx = -ASX_MAX_DELTA; }
    if (Dy > ASX_MAX_DELTA)  { Dy = ASX_MAX_DELTA; }
    if (Dy < -ASX_MAX_DELTA) { Dy = -ASX_MAX_DELTA; }

    if (KeGetCurrentIrql() < DISPATCH_LEVEL) {
        KeRaiseIrql(DISPATCH_LEVEL, &old);
        raised = TRUE;
    }

    //
    // Held across the call: the only other writer of Filter is the disconnect
    // path, and it must not pull the callback out from under us mid-emit.
    //
    KeAcquireSpinLockAtDpcLevel(&g_Asx.Lock);

    ctx = g_Asx.Filter;
    if (ctx != NULL && ctx->Upper.ClassService != NULL) {

        RtlZeroMemory(&d, sizeof(d));
        d.UnitId = ctx->UnitId;
        d.Flags = MOUSE_MOVE_RELATIVE;
        d.ButtonFlags = Buttons;
        d.ButtonData = (USHORT)Data;
        d.LastX = Dx;
        d.LastY = Dy;
        d.ExtraInformation = 0;

        for (i = 0; i < RTL_NUMBER_OF(AsxDownBits); i++) {
            USHORT down = AsxDownBits[i];
            if (Buttons & down) {
                g_Asx.HeldButtons |= down;
            }
            if (Buttons & (USHORT)(down << 1)) {
                g_Asx.HeldButtons &= (USHORT)~down;
            }
        }

        ((PSERVICE_CALLBACK_ROUTINE)ctx->Upper.ClassService)(
            ctx->Upper.ClassDeviceObject, &d, &d + 1, &taken);
    }

    KeReleaseSpinLockFromDpcLevel(&g_Asx.Lock);

    if (raised) {
        KeLowerIrql(old);
    }
}

static VOID
AsxReleaseHeld(VOID)
{
    USHORT held = g_Asx.HeldButtons;
    ULONG  i;

    for (i = 0; i < RTL_NUMBER_OF(AsxDownBits); i++) {
        USHORT down = AsxDownBits[i];
        if (held & down) {
            AsxEmit((USHORT)(down << 1), 0, 0, 0);
        }
    }
    g_Asx.HeldButtons = 0;
}

static VOID
AsxArm(
    _In_ ULONG DelayUs
    )
{
    LARGE_INTEGER now;
    LONGLONG      step = (LONGLONG)DelayUs * 10;    // us -> 100 ns
    LONGLONG      due;

    KeQuerySystemTimePrecise(&now);

    if (g_Asx.NextDue == 0) {
        g_Asx.NextDue = now.QuadPart;
    }
    g_Asx.NextDue += step;

    //
    // If we have fallen a long way behind - a storm of DPCs elsewhere, a
    // debugger break - rebase rather than firing a catch-up burst that would
    // arrive as one impossible jump.
    //
    if (g_Asx.NextDue < now.QuadPart - ASX_REBASE_100NS) {
        g_Asx.NextDue = now.QuadPart + step;
    }

    due = g_Asx.NextDue;
    if (due <= now.QuadPart) {
        due = now.QuadPart + 1;
    }

    ExSetTimer(g_Asx.Timer, due, 0, NULL);
}

static VOID
AsxTimerCallback(
    _In_ PEX_TIMER Timer,
    _In_opt_ PVOID Context
    )
{
    UNREFERENCED_PARAMETER(Timer);
    UNREFERENCED_PARAMETER(Context);

    for (;;) {

        ASX_STEP step = { 0 };
        BOOLEAN  have = FALSE;
        BOOLEAN  more = FALSE;
        BOOLEAN  release = FALSE;
        ULONG    nextDelay = 0;

        KeAcquireSpinLockAtDpcLevel(&g_Asx.PlayLock);
        if (g_Asx.Count != 0) {
            step = g_Asx.Ring[g_Asx.Head];
            g_Asx.Head = (g_Asx.Head + 1) % ASX_QUEUE_CAPACITY;
            g_Asx.Count--;
            have = TRUE;
            if (g_Asx.Count != 0) {
                more = TRUE;
                nextDelay = g_Asx.Ring[g_Asx.Head].DelayUs;
            }
        }
        KeReleaseSpinLockFromDpcLevel(&g_Asx.PlayLock);

        if (have) {
            AsxEmit(step.Buttons, step.Data, step.Dx, step.Dy);
            g_Asx.StepsEmitted++;
        }

        if (more) {
            if (nextDelay == 0) {
                continue;               // a zero delay means "in the same pass"
            }
            AsxArm(nextDelay);
            return;
        }

        //
        // Drained. Re-check under the lock in case a submit landed while we
        // were emitting: Playing must only go false with the ring empty, or
        // that submit would arm a second, concurrent drain.
        //
        KeAcquireSpinLockAtDpcLevel(&g_Asx.PlayLock);
        if (g_Asx.Count != 0) {
            nextDelay = g_Asx.Ring[g_Asx.Head].DelayUs;
            KeReleaseSpinLockFromDpcLevel(&g_Asx.PlayLock);
            if (nextDelay == 0) {
                continue;
            }
            AsxArm(nextDelay);
            return;
        }
        g_Asx.Playing = FALSE;
        release = g_Asx.ReleaseOnDrain;
        g_Asx.ReleaseOnDrain = FALSE;
        KeReleaseSpinLockFromDpcLevel(&g_Asx.PlayLock);

        if (release) {
            AsxReleaseHeld();
        }
        return;
    }
}

NTSTATUS
AsxSubmit(
    _In_reads_(Count) const ASX_STEP *Steps,
    _In_ ULONG Count,
    _In_ ULONG Flags
    )
{
    KIRQL   irql;
    ULONG   i;
    ULONG   accepted = 0;
    BOOLEAN start = FALSE;
    ULONG   firstDelay = 0;

    if (Count == 0 || Count > ASX_MAX_STEPS_PER_SUBMIT) {
        return STATUS_INVALID_PARAMETER;
    }
    if (g_Asx.Ring == NULL) {
        return STATUS_DEVICE_NOT_READY;
    }

    KeAcquireSpinLock(&g_Asx.PlayLock, &irql);

    if (Flags & ASX_SUBMIT_REPLACE) {
        g_Asx.Head = 0;
        g_Asx.Tail = 0;
        g_Asx.Count = 0;
    }

    for (i = 0; i < Count; i++) {
        if (g_Asx.Count == ASX_QUEUE_CAPACITY) {
            g_Asx.Dropped += (Count - i);
            break;
        }
        g_Asx.Ring[g_Asx.Tail] = Steps[i];
        g_Asx.Tail = (g_Asx.Tail + 1) % ASX_QUEUE_CAPACITY;
        g_Asx.Count++;
        accepted++;
    }

    if (Flags & ASX_SUBMIT_RELEASE) {
        g_Asx.ReleaseOnDrain = TRUE;
    }

    if (accepted != 0 && !g_Asx.Playing) {
        g_Asx.Playing = TRUE;
        g_Asx.NextDue = 0;                      // rebase the schedule on now
        firstDelay = g_Asx.Ring[g_Asx.Head].DelayUs;
        start = TRUE;
    }

    KeReleaseSpinLock(&g_Asx.PlayLock, irql);

    if (accepted == 0) {
        return STATUS_INSUFFICIENT_RESOURCES;
    }
    if (start) {
        AsxArm(firstDelay == 0 ? 1 : firstDelay);
    }
    return STATUS_SUCCESS;
}

VOID
AsxStop(
    _In_ BOOLEAN ReleaseHeld
    )
{
    KIRQL irql;

    KeAcquireSpinLock(&g_Asx.PlayLock, &irql);
    g_Asx.Head = 0;
    g_Asx.Tail = 0;
    g_Asx.Count = 0;
    g_Asx.ReleaseOnDrain = FALSE;
    KeReleaseSpinLock(&g_Asx.PlayLock, irql);

    if (g_Asx.Timer != NULL) {
        ExCancelTimer(g_Asx.Timer, NULL);
    }

    KeAcquireSpinLock(&g_Asx.PlayLock, &irql);
    g_Asx.Playing = FALSE;
    KeReleaseSpinLock(&g_Asx.PlayLock, irql);

    if (ReleaseHeld) {
        AsxReleaseHeld();
    } else {
        g_Asx.HeldButtons = 0;
    }
}
