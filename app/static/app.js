let runId=null;
const $ = id => document.getElementById(id);
function money(x){return "₹"+Number(x||0).toLocaleString("en-IN",{maximumFractionDigits:2})}
function esc(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]))}

function showError(msg){
  $("error").textContent=msg;
  $("error").classList.remove("hidden");
}
function clearError(){$("error").classList.add("hidden");$("error").textContent=""}
async function readJsonResponse(response){
  const text=await response.text();
  let data;
  try{data=text?JSON.parse(text):{}}catch{throw new Error(`Server returned non-JSON response (${response.status}): ${text.slice(0,300)}`)}
  if(!response.ok) throw new Error(data.error||data.detail||data.message||"Request failed");
  return data;
}
function safeText(value){return value===undefined||value===null||value===""?"Not available":typeof value==="object"?JSON.stringify(value):String(value)}

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
    const data=await readJsonResponse(r);
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
    const data=await readJsonResponse(r);
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
    const data=await readJsonResponse(r);
    render(data); $("stage").textContent="HUMAN APPROVED";
  }catch(e){showError(e.message)}
}

function render(data){
  $("stage").textContent=(data.stage||"").toUpperCase();
  $("offers").classList.remove("empty");
  const confirmedOffers=(data.offers||[]).filter(o=>o.compatible&&o.confirmed&&Number(o.quantity_available)>0);
  const noConfirmedMessage=confirmedOffers.length?"":`<div class="bad"><b>No supplier confirmed availability.</b></div>`;
  $("offers").innerHTML=noConfirmedMessage+(data.offers||[]).map(o=>`
    <div class="offer">
      <div><b>${esc(o.supplier)}</b><div class="meta">${esc(o.status)} · ${esc(o.delivery_method)} · ${o.availability_hours}h · ${money(o.unit_price)}/unit</div><div class="meta">${o.call_id?`CALL-E ${esc(o.call_id)} · `:""}${esc(o.phone||"")}</div><div class="meta">${esc(o.notes)}</div></div>
      <div class="stock">${o.quantity_available} units</div>
    </div>`).join("");

  if(data.maintenance){
    const technician=data.technician_intelligence||data.maintenance.technician_intelligence||{
      required:null,
      installation_required:data.maintenance.installation_required,
      installation_minutes:data.maintenance.installation_minutes,
      technicians_available:data.maintenance.technicians_available??data.request?.available_technicians,
      status:null
    };
    $("requiredTech").textContent=safeText(technician.required);
    $("installTime").textContent=technician.installation_required===true
      ? `Required${technician.installation_minutes?` · ${technician.installation_minutes} min`:""}`
      : technician.installation_required===false ? "Not required" : "Not configured";
    $("techniciansAvailable").textContent=technician.technicians_available===undefined||technician.technicians_available===null
      ? "Not configured" : String(technician.technicians_available);
    $("techStatus").textContent=(technician.status||"not configured").toUpperCase();
  }
  const p=data.recommended_plan;
  if(p){
    const planReasons=p.infeasibility_reasons?.length
      ? `<p class="bad">${p.infeasibility_reasons.map(esc).join("<br>")}</p>`
      : "<p class=\"good\"><b>WHY:</b> This plan meets the quantity, technician, and maximum recovery-time constraints.</p>";
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
        ${planReasons}
      </div>`;
    $("approveBtn").disabled=!p.feasible || data.approved;
    $("approval").textContent=data.approved?"✓ Recovery plan approved by human operator. No automated purchase was executed.":"No purchase initiated — approval is required.";
  }
}
async function loadConfig(){
  try{
    const r=await fetch("/api/config"); const c=await readJsonResponse(r);
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

async function testLiveCall(){
  clearError();
  const mode=$("mode").value;
  if(mode!=="live"){showError("Select LIVE CALL-E mode before testing a real phone call.");return}
  if(!$("liveConfirm").checked){showError("Check the LIVE authorization checkbox before placing a real phone call.");return}
  const phone=$("phone1").value.trim();
  if(!phone){showError("Enter Supplier A phone number first.");return}
  const supplierName="Supplier A";
  const btn=$("testCallBtn");
  btn.disabled=true; btn.textContent="CALL REQUESTING...";
  $("callStatus").classList.remove("hidden");
  $("callStatus").innerHTML=`<b>Sending live call request...</b><div class="meta">Supplier: ${esc(supplierName)}</div>`;
  try{
    const r=await fetch("/api/calle/test-call",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({phone,supplier_name:"Supplier A"})});
    const data=await readJsonResponse(r);
    const callId=data.call_id||data.id;
    if(!callId) throw new Error("CALL-E request returned successfully but no call ID was returned.");
    $("callStatus").innerHTML=`<b>CALL REQUEST SENT</b><div class="meta">Supplier: ${esc(supplierName)}</div><div class="meta">Phone: ${maskPhone(phone)}</div><div class="meta">CALL-E Call ID: ${esc(callId)}</div><div class="call-state">Status: ${esc(data.status||"queued").toUpperCase()}</div>`;
    await pollTestCall(callId,phone,supplierName);
  }catch(e){
    showError(e.message||"Unable to create CALL-E test call.");
    $("callStatus").classList.remove("hidden");
    $("callStatus").innerHTML=`<b>CALL-E CALL FAILED</b><div class="bad">${esc(e.message||"Unknown error")}</div>`;
  }finally{btn.disabled=false;btn.textContent="TEST LIVE CALL"}
}

function maskPhone(phone){
  const p=String(phone||"");
  if(p.length<=6)return "***";
  return p.slice(0,3)+"******"+p.slice(-4);
}

async function pollTestCall(callId,phone,supplierName){
  const maxAttempts=150;
  for(let attempt=0;attempt<maxAttempts;attempt++){
    try{
      const r=await fetch(`/api/calle/call/${encodeURIComponent(callId)}`); const data=await readJsonResponse(r);
      const status=String(data.status||data.call_status||"unknown").toLowerCase();
      let html=`<b>LIVE CALL-E STATUS</b><div class="meta">Supplier: ${esc(supplierName)}</div><div class="meta">Phone: ${maskPhone(phone)}</div><div class="meta">CALL-E Call ID: ${esc(callId)}</div><div class="call-state">Status: ${esc(status).toUpperCase()}</div>`;
      if(status==="failed"){
        const diagnostics=data.diagnostics||{};
        const failureCode=data.failure_code||diagnostics.failure_code||data.error_code||"UNKNOWN";
        const failureMessage=data.failure_message||diagnostics.failure_message||data.error||data.message||"CALL-E reported that the call failed.";
        const reason=diagnostics.human_message;
        const category=diagnostics.category;
        const friendly=category==="supplier_unavailable"?`<div class="bad"><b>Reason:</b> ${esc(reason)}</div><div><b>Telephony code:</b> ${esc(safeText(diagnostics.attempt_failure_code||failureCode))}</div><div><b>Original CALL-E message:</b> ${esc(failureMessage)}</div>`:"";
        $("callStatus").innerHTML=html+friendly+diagnosticHtml(failureCode,failureMessage,diagnostics); return;
      }
      if(status==="canceled"){$("callStatus").innerHTML=html+`<div class="bad">The CALL-E call was canceled.</div>`;return}
      if(status==="completed"){
        const result=data.structured_result||data.result||data.recipient_result||null;
        html+=`<div class="good"><b>CALL COMPLETED</b></div>`;
        if(result) html+=`<div class="call-result"><b>Supplier response</b><div>Available quantity: ${esc(safeText(result.available_quantity))}</div><div>Unit price: ${esc(safeText(result.unit_price))} ${esc(safeText(result.currency))}</div><div>Delivery: ${esc(safeText(result.delivery_hours))} hours</div><div>Fulfillment: ${esc(safeText(result.can_fulfill))}</div></div>`;
        $("callStatus").innerHTML=html; return;
      }
      $("callStatus").innerHTML=html;
      await new Promise(resolve=>setTimeout(resolve,2000));
    }catch(e){$("callStatus").innerHTML+=`<div class="bad">${esc(e.message)}</div>`;return}
  }
  $("callStatus").innerHTML+=`<div class="bad">CALL-E status polling timed out after 5 minutes.</div>`;
}
function diagnosticHtml(failureCode,failureMessage,diagnostics){
  return `<div class="bad"><b>Failure code:</b> ${esc(safeText(failureCode))}<br><b>Failure message:</b> ${esc(safeText(failureMessage))}</div><details class="technical-details"><summary>Technical details</summary><div>Recipient status: ${esc(safeText(diagnostics.recipient_status))}</div><div>Attempt status: ${esc(safeText(diagnostics.attempt_status))}</div><div>Attempt failure code: ${esc(safeText(diagnostics.attempt_failure_code))}</div><div>Attempt failure message: ${esc(safeText(diagnostics.attempt_failure_message))}</div><div>Structured result: ${diagnostics.structured_result&&Object.keys(diagnostics.structured_result).length?"Available":"Not available"}</div><div>Transcript: ${diagnostics.transcript_available?"Available":"Not available"}</div></details>`;
}
loadConfig();
