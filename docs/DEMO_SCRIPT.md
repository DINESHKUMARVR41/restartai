# RestartAI — 3-minute demo script

## 0:00–0:25 — Incident

Enter:

- CNC Production Line 4
- SKF 6205-2RS
- 20 units
- 3-hour maximum recovery
- ₹5,000/hour downtime
- 3 technicians

Say:

> “The line is down. We need 20 compatible bearings within three hours. Every hour costs ₹5,000. The problem is not just procurement — it is getting production restarted.”

## 0:25–1:05 — Initial supplier intelligence

Run in **DEMO** mode.

Show:

- A: 20 units, ₹150, 24h
- B: 20 units, ₹220, 2h
- C: 8 units, ₹180, 30-min pickup

Say:

> “RestartAI is not simply ranking suppliers by price. Supplier C is useful even though it cannot satisfy the full order.”

## 1:05–1:35 — Dynamic replanning

Click **REPLAN / CALL NEXT SUPPLIER**.

Supplier D appears:

- 12 units
- ₹190
- 90-minute delivery

Say:

> “The agent has learned that eight units are already covered, so the next call is context-aware: it is looking for the remaining twelve, not repeating the original generic request.”

## 1:35–2:10 — Technician intelligence

Show:

- Required technicians: 2
- Available: 3
- Installation: 30 min
- Status: FEASIBLE

Say:

> “Delivery is not the end of recovery. RestartAI also accounts for the technicians needed to install the part.”

Mention that the values come from a local **demo maintenance knowledge base** and are not universal industrial standards.

## 2:10–2:40 — Economics

Show C+D:

```text
Purchase = ₹3,720
Material arrival = 90 min
Installation = 30 min
Recovery = 2 hours

Downtime = ₹10,000
Total exposure = ₹13,720
```

Compare Supplier B:

```text
Purchase = ₹4,400
Recovery = 2 hours
Total exposure = ₹14,400
```

Say:

> “The engine uses deterministic Python arithmetic. The AI does not invent the final numbers.”

## 2:40–3:00 — Human approval

Click **APPROVE RECOVERY PLAN**.

Say:

> “The agent recommends the recovery plan, but a human remains in control. No purchase is automatically executed.”

Close with:

> “We did not find the cheapest supplier. We found the fastest feasible way to restart production.”
