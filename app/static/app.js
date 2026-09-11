let runId=null;

function money(x){return "₹"+Number(x||0).toLocaleString("en-IN")}
function esc(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]))}

async function startRecovery(){
  const suppliers=[
    {name:"Supplier A",phone:"+10000000001"},
    {name:"Supplier B",phone:"+10000000002"},
    {name:"Supplier C",phone:"+10000000003"},
    {name:"Supplier D",phone:"+10000000004"}
  ];
  const payload={
    machine:document.getElementById("machine").value,
    part_number:document.getElementById("part").value,
    part_description:document.getElementById("desc").value,
    quantity:Number(document.getElementById("qty").value),
    max_hours:Number(document.getElementById("maxHours").value),
    downtime_cost_per_hour:Number(document.getElementById("downtime").value),
    compatibility_notes:document.getElementById("compat").value,
    suppliers
  };
  document.getElementById("stage").textContent="CALLING";
  const r=await fetch("/api/recovery/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const data=await r.json();
  runId=data.run_id;
  render(data);
  document.getElementById("replanPanel").classList.remove("hidden");
  document.getElementById("replanMessage").innerHTML="<b>Partial stock detected.</b> The first supplier wave did not provide a complete fast solution. The agent can now call the fourth supplier for the missing quantity.";
}

async function replan(){
  document.getElementById("stage").textContent="REPLANNING";
  document.getElementById("replanMessage").innerHTML="Calling the next supplier and recomputing recovery plans…";
  const r=await fetch(`/api/recovery/${runId}/replan`,{method:"POST"});
  const data=await r.json();
  render(data);
  document.getElementById("planPanel").classList.remove("hidden");
  document.getElementById("replanMessage").innerHTML="<b>Replan complete.</b> The agent evaluated single-supplier and split-supplier recovery plans using the slowest required leg as the recovery bottleneck.";
}

async function approve(){
  const r=await fetch(`/api/recovery/${runId}/approve`,{method:"POST"});
  const data=await r.json();
  render(data);
  document.getElementById("stage").textContent="HUMAN APPROVED";
  document.getElementById("plan").insertAdjacentHTML("afterbegin",'<div class="approved-text">✓ Recovery plan approved by human operator. No automated purchase was executed.</div>');
}

function render(data){
  document.getElementById("stage").textContent=data.stage.toUpperCase();
  document.getElementById("offers").classList.remove("empty");
  document.getElementById("offers").innerHTML=(data.offers||[]).map(o=>`
    <div class="offer">
      <div><b>${esc(o.supplier)}</b><div class="meta">${esc(o.delivery_method)} · ${o.availability_hours}h · ${money(o.unit_price)}/unit</div><div class="meta">${esc(o.notes)}</div></div>
      <div class="stock">${o.quantity_available} units</div>
    </div>`).join("");

  if(data.recommended_plan){
    const p=data.recommended_plan;
    document.getElementById("plan").innerHTML=`
      <div class="plan-card">
        <h3>Fastest feasible recovery plan</h3>
        <div class="plan-metrics">
          <div class="metric"><small>Recovery</small><b>${p.recovery_time_hours}h</b></div>
          <div class="metric"><small>Parts</small><b>${money(p.total_purchase_cost)}</b></div>
          <div class="metric"><small>Downtime exposure</small><b>${money(p.downtime_exposure)}</b></div>
          <div class="metric"><small>Total exposure</small><b>${money(p.total_exposure)}</b></div>
        </div>
        ${(p.legs||[]).map(l=>`<div class="leg"><span><b>${esc(l.supplier)}</b> · ${l.quantity} units</span><span>${money(l.unit_price)}/unit · ${l.arrival_hours}h</span></div>`).join("")}
      </div>`;
  }
}
