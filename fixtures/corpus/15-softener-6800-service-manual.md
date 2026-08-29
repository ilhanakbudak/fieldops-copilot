---
title: NG-6800 Twin-Tank Water Softener — Service Manual
doc_type: manual
allowed_roles: [technician]
---

# NG-6800 Twin-Tank Water Softener — Service Manual

Northgate Water Systems · Revision A · Applies to serial numbers NG68-0100 and above.

---

## 1. Overview

The NG-6800 is a twin-tank alternating softener rated for 64,000 grains of
hardness capacity across two 12-inch resin tanks. One tank is always in service
while the other regenerates, so the unit never puts hard water into the house —
which is the whole reason a customer pays for it over an NG-4200.

Service intervals assume municipal feed water below 25 grains per gallon and an
iron content below 1.0 ppm. Above either figure, halve the interval.

---

## 2. Error Codes

The control head displays a two-character code with an `E-` prefix. The codes
are **not** the same as the NG-4200's. A technician reading an NG-4200 code
chart at an NG-6800 will diagnose the wrong fault.

### E-01 Motor Stall

Same cause as the single-tank units: the drive motor is drawing stall current.
Check the cam follower before replacing the motor.

### E-07 Tank Switch Failure

The controller commanded a changeover and the position sensor did not confirm
it inside 90 seconds. This code does not exist on the NG-4200, which has nothing
to change over to.

Check the changeover valve stem for scale, then the position sensor connector at
the rear of the head. A unit left in this state runs one tank continuously and
will produce hard water when that tank exhausts.

### E-11 Both Tanks In Regeneration

A software interlock failure. Power-cycle the head; if it returns, the control
board is finished. Do not attempt to force a changeover manually with both tanks
in brine draw.

---

## 3. Regeneration Cycle

A full regeneration takes 74 minutes at 60 psi, per tank:

| Stage | Duration | Purpose |
|---|---|---|
| Backwash | 8 min | Lift and rinse the resin bed |
| Brine draw | 45 min | Draw brine through the injector and displace hardness |
| Slow rinse | 8 min | Rinse remaining brine from the bed |
| Fast rinse | 6 min | Settle and repack the bed |
| Brine refill | 7 min | Refill the cabinet for the next cycle |

Shorter than the NG-4200's 96 minutes because the tanks are smaller
individually. Interrupting a regeneration is not the problem it is on a
single-tank unit — the other tank stays in service — but the interrupted tank
resumes from its stage on the next power-up and until it finishes the unit has
no reserve.

---

## 4. Resin Replacement

Expect eight to twelve years on municipal water, four to six on well water with
measurable iron or chlorine above 2 ppm.

Symptoms of exhausted resin: hardness leakage that does not improve after a
manual regeneration, and a bed that has visibly lost volume. Confirm with a
hardness test on the outlet immediately after a full regeneration.

Use NG-RES-12 for the 12-inch tanks — **two of them**, one per tank, and they
must be replaced as a pair. A unit with one fresh tank and one exhausted tank
alternates between soft and hard water on a cycle the customer will describe as
intermittent and nobody will be able to reproduce on a single test.
