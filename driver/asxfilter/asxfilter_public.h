/*++

asxfilter_public.h

    Interface between the Attack Shark X3 mouse filter driver and user mode.
    This header is the whole contract: include it from a C client, or read the
    structure layouts straight off it for a ctypes/FFI binding.

    The driver is an upper filter on the X3's own mouse device stack. Movement
    submitted here is handed to mouclass through the same service callback
    mouhid uses for physical reports, so it carries no injection flag and is
    attributed to the X3's own device handle. See docs/DRIVER.md.

--*/

#ifndef ASXFILTER_PUBLIC_H
#define ASXFILTER_PUBLIC_H

//
// Control device. Opened with CreateFile from user mode; the security
// descriptor in the driver restricts this to SYSTEM and Administrators,
// because anything holding this handle can drive the mouse.
//
#define ASX_NT_DEVICE_NAME      L"\\Device\\AttackSharkFilter"
#define ASX_SYMBOLIC_NAME       L"\\DosDevices\\AttackSharkFilter"
#define ASX_USER_PATH           L"\\\\.\\AttackSharkFilter"

#define ASX_INTERFACE_VERSION   0x00010000UL    // 1.0

//
// 0x8000 and up is the range reserved for third parties.
//
#define ASX_DEVICE_TYPE         0x8ADFUL

#define IOCTL_ASX_STATUS \
    CTL_CODE(ASX_DEVICE_TYPE, 0x800, METHOD_BUFFERED, FILE_READ_ACCESS)
#define IOCTL_ASX_SUBMIT \
    CTL_CODE(ASX_DEVICE_TYPE, 0x801, METHOD_BUFFERED, FILE_WRITE_ACCESS)
#define IOCTL_ASX_STOP \
    CTL_CODE(ASX_DEVICE_TYPE, 0x802, METHOD_BUFFERED, FILE_WRITE_ACCESS)
#define IOCTL_ASX_SET_FILTER \
    CTL_CODE(ASX_DEVICE_TYPE, 0x803, METHOD_BUFFERED, FILE_WRITE_ACCESS)
#define IOCTL_ASX_READ_EVENTS \
    CTL_CODE(ASX_DEVICE_TYPE, 0x804, METHOD_BUFFERED, FILE_READ_ACCESS)

//
// Button transitions. These values are MOUSE_INPUT_DATA's ButtonFlags
// verbatim (ntddmou.h), repeated here so a user-mode client does not need the
// DDK. Button 4 is "forward" / XBUTTON2, button 5 is "back" / XBUTTON1 - the
// same numbering the vendor UI and attackshark's button map use.
//
#define ASX_LEFT_DOWN           0x0001
#define ASX_LEFT_UP             0x0002
#define ASX_RIGHT_DOWN          0x0004
#define ASX_RIGHT_UP            0x0008
#define ASX_MIDDLE_DOWN         0x0010
#define ASX_MIDDLE_UP           0x0020
#define ASX_BUTTON4_DOWN        0x0040
#define ASX_BUTTON4_UP          0x0080
#define ASX_BUTTON5_DOWN        0x0100
#define ASX_BUTTON5_UP          0x0200
#define ASX_WHEEL               0x0400      // ButtonData = delta, 120 per notch
#define ASX_HWHEEL              0x0800      // ButtonData = delta

#define ASX_ALL_BUTTONS         0x03FFU

//
// One emitted report. A step may carry movement and a button transition at
// once, exactly as a real mouse report does.
//
//   DelayUs   microseconds to wait after the previous step before emitting
//             this one. 0 emits it in the same pass as its predecessor.
//   Dx, Dy    relative movement, screen pixels after pointer acceleration.
//   Buttons   zero or more ASX_* transitions, OR'd.
//   Data      wheel delta when ASX_WHEEL / ASX_HWHEEL is set, else 0.
//
typedef struct _ASX_STEP {
    unsigned long   DelayUs;
    long            Dx;
    long            Dy;
    unsigned short  Buttons;
    short           Data;
} ASX_STEP, *PASX_STEP;                                 // 16 bytes

#define ASX_MAX_STEPS_PER_SUBMIT    4096UL
#define ASX_QUEUE_CAPACITY          16384UL     // ~16 s of 1 kHz motion
#define ASX_MAX_DELTA               16384L      // sanity clamp per step

//
// IOCTL_ASX_SUBMIT input: this header followed by Count ASX_STEP records.
//
#define ASX_SUBMIT_REPLACE      0x00000001UL    // discard whatever is queued
#define ASX_SUBMIT_RELEASE      0x00000002UL    // release held buttons when done

typedef struct _ASX_SUBMIT {
    unsigned long   Count;
    unsigned long   Flags;
    ASX_STEP        Steps[1];               // Count entries
} ASX_SUBMIT, *PASX_SUBMIT;

//
// IOCTL_ASX_SET_FILTER input. Governs what the driver does with the *physical*
// reports coming up from mouhid, which is the other half of "full control":
// a button bound to a macro can be swallowed here so no application ever sees
// it, without a user-mode hook.
//
// Config is deliberately volatile: it is reset when the last handle to the
// control device closes, so a crashed daemon can never leave the mouse
// crippled.
//
typedef struct _ASX_FILTER_CFG {
    unsigned long   SuppressButtons;    // ASX_* transitions to swallow
    unsigned long   SuppressMove;       // non-zero: drop physical movement
    unsigned long   ReportEvents;       // non-zero: queue for READ_EVENTS
} ASX_FILTER_CFG, *PASX_FILTER_CFG;

//
// IOCTL_ASX_READ_EVENTS output: an array of these. The call pends until at
// least one event is available (an inverted call), so a trigger costs no
// polling and arrives before mouclass sees it.
//
typedef struct _ASX_EVENT {
    unsigned __int64 Time;              // interrupt time, 100 ns units
    unsigned short   Buttons;
    short            Data;
    long             Dx;
    long             Dy;
    unsigned long    Suppressed;        // non-zero: withheld from mouclass
} ASX_EVENT, *PASX_EVENT;                            // 24 bytes

//
// IOCTL_ASX_STATUS output.
//
typedef struct _ASX_STATUS {
    unsigned long    Version;           // ASX_INTERFACE_VERSION
    unsigned long    Attached;          // filter is bound to a mouse stack
    unsigned long    Connected;         // IOCTL_INTERNAL_MOUSE_CONNECT seen
    unsigned long    Playing;
    unsigned long    Queued;
    unsigned long    Capacity;
    unsigned long    SuppressButtons;
    unsigned long    SuppressMove;
    unsigned __int64 StepsEmitted;
    unsigned __int64 PhysicalReports;
    unsigned __int64 Dropped;           // steps discarded by a full queue
} ASX_STATUS, *PASX_STATUS;

#endif // ASXFILTER_PUBLIC_H
