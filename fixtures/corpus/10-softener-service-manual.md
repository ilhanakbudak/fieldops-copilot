---
title: NG-4200 Water Softener — Service Manual
doc_type: manual
allowed_roles: [technician]
---

# NG-4200 Water Softener — Service Manual

Northgate Water Systems · Revision C · Applies to serial numbers NG42-0400 and above.

---

## 1. Overview

The NG-4200 is a demand-initiated regeneration softener rated for 32,000 grains
of hardness capacity. It uses a metered control valve, a 10-inch resin tank and a
separate 18-by-33-inch brine cabinet.

Service intervals assume municipal feed water below 25 grains per gallon and an
iron content below 1.0 ppm. Above either figure, halve the interval.

---

## 2. Error Codes

The control head displays a two-character code with an `E-` prefix. Codes clear
when the fault condition is resolved and the head is power-cycled.

### E-01 Motor Stall

The valve motor drew more than 1.4 A for longer than four seconds. Almost always
a mechanical obstruction in the rotor disc, not a motor fault. Inspect the disc
for resin fines before replacing the motor.

### E-02 Position Error

The control head could not confirm a valve position within 90 seconds. Check the
optical position sensor for brine film. Cleaning the sensor window with a dry
cloth resolves the majority of cases.

### E-04 Brine Valve Fault

The brine draw cycle completed without the expected drop in brine tank level.
This is the most common code in the field and it is very rarely the valve
itself.

In order of likelihood:

1. Salt bridge in the brine cabinet — a hardened crust spanning the tank, with
   an empty space beneath it. Break it with a broom handle, never with a metal
   bar.
2. Blocked injector or injector screen. Remove, soak in citric acid solution for
   ten minutes, refit.
3. Kinked or collapsed brine line between the cabinet and the control head.
4. Brine valve float assembly stuck in the closed position.
5. Failed brine valve. Replace part NG-BV-14.

A unit reporting E-04 will continue to supply water. It is supplying *unsoftened*
water, which is why customers frequently report the symptom before the code:
scale returning to fixtures, soap not lathering.

### E-14 Reserve Capacity Exceeded

Distinct from E-04 despite the similar code. The unit consumed its calculated
reserve before the scheduled regeneration. Either the hardness setting is too
low for the actual feed water, or household consumption has increased. Re-test
the raw water and reprogram the hardness value; do not simply shorten the
regeneration interval, which wastes salt and does not address the cause.

### E-21 Flow Sensor No Signal

No pulses from the turbine meter for seven days while the house was occupied.
Check the meter cable at the control head first — the connector backs out under
vibration.

---

## 3. Regeneration Cycle

A full regeneration takes 96 minutes at 60 psi:

| Stage | Duration | Purpose |
|---|---|---|
| Backwash | 10 min | Lift and rinse the resin bed |
| Brine draw | 60 min | Draw brine through the injector and displace hardness |
| Slow rinse | 10 min | Rinse remaining brine from the bed |
| Fast rinse | 8 min | Settle and repack the bed |
| Brine refill | 8 min | Refill the cabinet for the next cycle |

Interrupting a regeneration mid-brine-draw leaves the bed partially loaded. The
unit will resume from the interrupted stage on the next power-up, but the
customer will notice hard water in the interim.

---

## 4. Resin Replacement

Expect eight to twelve years on municipal water, four to six on well water with
measurable iron or chlorine above 2 ppm.

Symptoms of exhausted resin: hardness leakage that does not improve after a
manual regeneration, and a bed that has visibly lost volume. Confirm with a
hardness test on the outlet immediately after a full regeneration — above 1
grain per gallon on a freshly regenerated bed means the resin is finished.

Use NG-RES-10 for the 10-inch tank. Two cubic feet, delivered dry.
