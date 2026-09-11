let runId=null;

const $ = id => document.getElementById(id);
function money(x){return "₹"+Number(x||0).toLocaleString("en-IN",{maximumFractionDigits:2})}
function esc(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]))}

function showError(msg){
  $("error").textContent=msg;
  $("error").classList.remove("hidden");
}
function clearError(){$("error").classList.add("hidden");$("error").textContent=""}

function supplierPayload(){
  return [1,2,3,4].map((i)=>({
    name:`Supplier ${String.fromCharCode(64+i)}`,
    phone: $(`phone${i}`).value.trim(),
    region:"IN", locale:"en-IN"
  }));
}

async function startRecovery(){
  clearError();
  const live=$("mode").value==="live";
  if(live && !$("liveConfirm").checked){
    showError("LIVE mode requires explicit confirmation that real phone calls will be placed.");
    return;
  }
  const payload={
    machine:$("machine").value.trim(), part_number:$("part").value.trim(),
    part_description:$("desc").value.trim(), quantity:Number($("qty").value),
    max_hours:Number($("maxHours").value), downtime_cost_per_hour:Number($("downtime").value),
    compatibility_notes:$("compat").value, available_technicians:Number($("techs").value),
    required_technicians_override:$("techOverride").value?Number($("techOverride").value):null,
    installation_minutes_override:$("installOverride").value?Number($("installOverride").value):null,
    suppliers:supplierPayload(), live_confirmed:live && $("liveConfirm").checked,
    idempotency_key:`${Date.now()}-${crypto.randomUUID()}`
  };
  $("stage").textContent="CALLING";
  $("startBtn").disabled=true;
  try{
    const r=await fetch("/api/recovery/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const data=await r.json(); if(!r.ok) throw new Error(data.error||"Recovery could not start.");
    runId=data.run_id; render(data);
    $("replanPanel").classList.remove("hidden");
    $("replanMessage").innerHTML=data.next_supplier_available
      ? "<b>Initial wave complete.</b> The agent will call the next configured supplier only if a shortfall remains."
      : "<b>No additional supplier is configured.</b> Replanning will still calculate the best available plan.";
  }catch(e){showError(e.message)} finally{$("startBtn").disabled=false}
}

async function replan(){
  if(!runId)return;
  clearError(); $("stage").textContent="REPLANNING"; $("replanMessage").textContent="Calling the next supplier using the discovered shortfall…";
  try{
    const r=await fetch(`/api/recovery/${runId}/replan`,{method:"POST"});
    const data=await r.json(); if(!r.ok) throw new Error(data.error||"Replanning failed.");
    render(data); $("planPanel").classList.remove("hidden");
    $("replanMessage").innerHTML=data.next_supplier_available
      ? "<b>One adaptive supplier call completed.</b> Replan again to continue discovery if a shortfall remains."
      : "<b>Replan complete.</b> All configured suppliers have been considered.";
  }catch(e){showError(e.message)}
}

async function approve(){
  if(!runId)return;
  clearError();
  try{
    const r=await fetch(`/api/recovery/${runId}/approve`,{method:"POST"});
    const data=await r.json(); if(!r.ok) throw new Error(data.error||"Approval failed.");
    render(data); $("stage").textContent="HUMAN APPROVED";
  }catch(e){showError(e.message)}
}

function render(data){
  $("stage").textContent=(data.stage||"").toUpperCase();
  $("offers").classList.remove("empty");
  $("offers").innerHTML=(data.offers||[]).map(o=>`
    <div class="offer">
      <div><b>${esc(o.supplier)}</b><div class="meta">${esc(o.status)} · ${esc(o.delivery_method)} · ${o.availability_hours}h · ${money(o.unit_price)}/unit</div><div class="meta">${esc(o.notes)}</div></div>
      <div class="stock">${o.quantity_available} units</div>
    </div>`).join("");

  if(data.maintenance){
    $("requiredTech").textContent=data.maintenance.required_technicians;
    $("installTime").textContent=`${data.maintenance.installation_minutes} min`;
    $("techStatus").textContent=data.maintenance.required_technicians <= data.request.available_technicians ? "FEASIBLE" : "TECHNICIAN SHORTAGE";
  }
  const p=data.recommended_plan;
  if(p){
    $("plan").innerHTML=`
      <div class="plan-card ${p.feasible?"":"not-feasible"}">
        <h3>${p.feasible?"Recommended recovery plan":"No feasible recovery plan"}</h3>
        <div class="plan-metrics">
          <div class="metric"><small>Recovery</small><b>${p.recovery_time_hours}h</b></div>
          <div class="metric"><small>Purchase</small><b>${money(p.total_purchase_cost)}</b></div>
          <div class="metric"><small>Downtime</small><b>${money(p.downtime_exposure)}</b></div>
          <div class="metric"><small>Total exposure</small><b>${money(p.total_exposure)}</b></div>
        </div>
        ${(p.legs||[]).map(l=>`<div class="leg"><span><b>${esc(l.supplier)}</b> · ${l.quantity} units</span><span>${money(l.unit_price)}/unit · ${l.arrival_hours}h</span></div>`).join("")}
        <p><b>Material arrival:</b> ${p.material_arrival_hours}h · <b>Installation:</b> ${p.installation_minutes} min · <b>Technicians:</b> ${p.required_technicians}/${p.available_technicians}</p>
        ${p.infeasibility_reasons?.length?`<p class="bad">${p.infeasibility_reasons.map(esc).join("<br>")}</p>`:"<p class="good"><b>WHY:</b> This plan meets the quantity, technician, and maximum recovery-time constraints.</p>"}
      </div>`;
    $("approveBtn").disabled=!p.feasible || data.approved;
    $("approval").textContent=data.approved?"✓ Recovery plan approved by human operator. No automated purchase was executed.":"No purchase initiated — approval is required.";
  }
}
async function loadConfig(){
  try{
    const r=await fetch("/api/config"); const c=await r.json();
    $("mode").value=c.mode;
    updateModeUI();
  }catch{}
}
function updateModeUI(){
  const live=$("mode").value==="live";
  $("modePill").textContent=live?"MODE: LIVE CALL-E":"MODE: DEMO";
  $("liveBox").classList.toggle("hidden",!live);
  document.querySelectorAll(".phone").forEach(x=>x.disabled=false);
}
loadConfig();
