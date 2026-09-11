# 3-minute demo script

## 0:00–0:25 — Problem

“Production is down. We need 20 compatible bearings within 3 hours. Every hour costs ₹5,000. The supplier portals don't give us enough information, so the agent has to call.”

Enter the incident.

## 0:25–1:10 — CALL-E

Show supplier cards appearing.

A: 20 units tomorrow.
B: 20 units in 2 hours.
C: only 8 units now.

Say:

“Most procurement agents would rank these offers. RestartAI has a different objective: restore production.”

## 1:10–1:45 — Replanning

Click REPLAN.

D: 12 units in 90 minutes.

Say:

“Supplier C is incomplete, but that doesn't mean it is useless. The agent changed the problem from finding one supplier to constructing a recovery plan.”

## 1:45–2:25 — Decision

Show C + D.

Explain:
- C supplies 8 now.
- D supplies 12 in 90 minutes.
- The slowest required leg determines recovery time.
- Purchase cost + downtime exposure are evaluated together.

## 2:25–2:45 — Human approval

Click approval.

Say:

“RestartAI recommends the plan, but it does not purchase anything without human approval.”

## 2:45–3:00 — Closing

“We didn't find the cheapest supplier. We found the fastest feasible way to restart production.”

CALL-E is the bridge to suppliers that have no API or structured inventory interface, and the agent adapts later calls using what it learned earlier.
