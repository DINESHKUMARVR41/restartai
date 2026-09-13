
const $=(id)=>document.getElementById(id);
function esc(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function safeText(v){return v==null||v===""?"—":String(v);}
function money(v){return v==null||v===""?"—":`₹${Number(v).toLocaleString("en-IN",{maximumFractionDigits:2})}`;}
function clearError(){const e=$("error");if(e){e.textContent="";e.classList.add("hidden");}}
function showError(msg){const e=$("error");if(e){e.textContent=msg||"Something went wrong.";e.classList.remove("hidden");}else{console.error(msg);}}
async function readJsonResponse(r){
  let d={}; try{d=await r.json();}catch{throw new Error(`Server returned HTTP ${r.status}.`);}
  if(!r.ok) throw new Error(d.error||d.detail||`Request failed (HTTP ${r.status}).`);
  return d;
}
let maintenanceTimer=null;
function scheduleMaintenanceIntelligence(){
  clearTimeout(maintenanceTimer); maintenanceTimer=setTimeout(loadMaintenanceIntelligence,250);
}
async function loadMaintenanceIntelligence(){
  const pn=$("part")?.value.trim()||"", pd=$("desc")?.value.trim()||"";
  if(!pn||!pd)return;
  try{
    const qs=new URLSearchParams({part_number:pn,part_description:pd,available_technicians:String(Number($("techs")?.value||0))});
    const tro=$("techOverride")?.value.trim(), io=$("installOverride")?.value.trim();
    if(tro)qs.set("required_technicians_override",tro);
    if(io)qs.set("installation_minutes_override",io);
    const r=await fetch(`/api/maintenance/intelligence?${qs}`);
    const d=await readJsonResponse(r), box=$("maintenanceInsight");
    if(box){
      box.classList.remove("hidden");
      box.innerHTML=`<b>Maintenance intelligence</b><div class="meta">${esc(d.configured?`Matched task: ${d.task}`:"No matching maintenance task configured")}</div>
      <div class="meta">Technicians: ${safeText(d.required_technicians)} required / ${safeText(d.technicians_available)} available · Installation: ${safeText(d.installation_minutes)} min · Status: ${esc(d.status||"unknown")}</div>`;
    }
  }catch(e){console.warn("Maintenance intelligence:",e.message);}
}
async function getAIRecommendation(){await refreshAI();}
async function downloadReport(){
  if(!runId){showError("Start a recovery run before downloading a report.");return;}
  try{
    const r=await fetch(`/api/recovery/${encodeURIComponent(runId)}/report`), d=await readJsonResponse(r);
    const blob=new Blob([JSON.stringify(d.report||d,null,2)],{type:"application/json"});
    const a=document.createElement("a"); a.href=URL.createObjectURL(blob); a.download=`restartai-recovery-${runId}.json`;
    document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(a.href),1000);
  }catch(e){showError(e.message);}
}

let runId=null;
let currentRunData=null;
function supplierPayload(){
  return [1,2,3,4].map((i)=>({
    name:`Supplier ${String.fromCharCode(64+i)}`,
    phone: $(`phone${i}`).value.trim(),
    region:"IN", locale:"en-IN"
  })).filter(s=>s.phone);
}

async function startRecovery(){
  clearError();
  const live=$("mode").value==="live";
  if(live && !$("liveConfirm").checked){
    showError("LIVE mode requires explicit confirmation that real phone calls will be placed.");
    return;
  }
  const suppliers=supplierPayload();
  if(live && !suppliers.length){showError("Enter at least one authorized supplier phone number.");return;}
  if(live){
    const bad=suppliers.find(s=>!/^\+[1-9]\d{7,14}$/.test(s.phone));
    if(bad){showError(`${bad.name} must use E.164 format, for example +919876543210.`);return;}
  }
  const techOverride=$("techOverride").value.trim();
  const installOverride=$("installOverride").value.trim();
  const payload={
    machine:$("machine").value.trim(), part_number:$("part").value.trim(),
    part_description:$("desc").value.trim(), quantity:Number($("qty").value),
    max_hours:Number($("maxHours").value), downtime_cost_per_hour:Number($("downtime").value),
    compatibility_notes:$("compat").value, available_technicians:Number($("techs").value),
    required_technicians_override:techOverride?Number(techOverride):null,
    installation_minutes_override:installOverride?Number(installOverride):null,
    suppliers:suppliers, live_confirmed:live && $("liveConfirm").checked,
    idempotency_key:`${Date.now()}-${crypto.randomUUID()}`
  };
  $("stage").textContent="CALLING";
  $("startBtn").disabled=true;
  try{
    const r=await fetch("/api/recovery/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const data=await readJsonResponse(r);
    runId=data.run_id; currentRunData=data; render(data); await refreshAI();
    $("replanPanel").classList.remove("hidden");
    $("replanMessage").innerHTML=data.agent_actions?.length
      ? `<b>Agent action:</b> ${esc(data.agent_actions[data.agent_actions.length-1])}`
      : data.next_supplier_available
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
    currentRunData=data; render(data); $("planPanel").classList.remove("hidden"); await refreshAI();
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
    currentRunData=data; render(data); $("stage").textContent="HUMAN APPROVED"; await refreshAI();
  }catch(e){showError(e.message)}
}

function render(data){
  $("stage").textContent=(data.stage||"").toUpperCase();
  $("offers").classList.remove("empty");
  const confirmedOffers=(data.offers||[]).filter(o=>o.compatible&&o.confirmed&&Number(o.quantity_available)>0);
  const noConfirmedMessage=confirmedOffers.length?"":`<div class="bad"><b>No supplier confirmed availability.</b></div>`;
  $("offers").innerHTML=noConfirmedMessage+(data.offers||[]).map(o=>`
    <div class="offer">
      <div><b>${esc(o.supplier)}</b><div class="meta">${esc(o.status)} · ${esc(o.delivery_method)} · ${safeText(o.availability_hours)}${o.availability_hours!=null?"h":""} · ${o.unit_price==null?"Not available":`${money(o.unit_price)}/unit`}</div><div class="meta">${o.call_id?`CALL-E ${esc(o.call_id)} · `:""}${esc(o.phone||"")}</div>
      <div class="meta">Match: ${esc(o.product_match||"unknown")} · Tax: ${esc(o.tax_included||"unknown")} · Shipping: ${o.shipping_cost==null?"unknown":money(o.shipping_cost)}</div>
      <div class="meta">${o.stock_location?`Stock: ${esc(o.stock_location)} · `:""}${o.payment_terms?`Terms: ${esc(o.payment_terms)} · `:""}${o.additional_charges?`Extra: ${esc(o.additional_charges)}`:""}</div>
      <div class="meta">${esc(o.notes)}</div></div>
      <div class="stock">${safeText(o.quantity_available)}${o.quantity_available!=null?" units":""}</div>
    </div>`).join("");
  if(data.agent_actions?.length) $("replanMessage").innerHTML=`<b>Agent action:</b> ${esc(data.agent_actions[data.agent_actions.length-1])}`;

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
async function refreshAI(){
  if(!runId)return;
  $("aiPanel").classList.remove("hidden"); $("aiStatus").textContent="ANALYZING";
  $("aiDecision").innerHTML='<div class="ai-loading">Gemini and Groq are evaluating confirmed supplier offers…</div>';
  try{
    const r=await fetch(`/api/ai/recommend/${encodeURIComponent(runId)}`,{method:"POST"});
    const d=await readJsonResponse(r);
    $("aiStatus").textContent=d.fallback_used?"RULE-BASED FALLBACK":"AI EVALUATED";
    if(!d.success){$("aiDecision").innerHTML=`<div class="bad">${esc(d.decision||"No confirmed supplier offer yet.")}</div>`;return;}
    const providers=[d.providers?.gemini?.used?"Gemini ✓":d.providers?.gemini?.configured?"Gemini unavailable":"Gemini not configured",
      d.providers?.groq?.used?"Groq ✓":d.providers?.groq?.configured?"Groq unavailable":"Groq not configured"].join(" · ");
    $("aiDecision").innerHTML=`<div class="ai-best"><small>BEST CONFIRMED OPTION</small><b>${esc(d.best_supplier||"No supplier")}</b><p>${esc(d.decision||"")}</p></div>
      <div class="ai-provider">${esc(providers)}</div>
      <div class="ai-ranking">${(d.ranking||[]).map((x,i)=>`<div class="ai-rank"><span><b>#${i+1} ${esc(x.supplier)}</b><small>${esc(x.reason||"")}</small></span><strong>${esc(x.score)}</strong></div>`).join("")}</div>
      <div class="ai-risk"><b>Risk:</b> ${esc(d.risk||"None returned")}</div>`;
  }catch(e){$("aiStatus").textContent="ERROR";$("aiDecision").innerHTML=`<div class="bad">${esc(e.message)}</div>`;}
}
function addChat(role,text){
  const d=document.createElement("div"); d.className=`chat-msg ${role}`; d.textContent=text;
  $("chatMessages").appendChild(d); $("chatMessages").scrollTop=$("chatMessages").scrollHeight; return d;
}
async function sendChat(){
  const input=$("chatInput"), msg=input.value.trim(); if(!msg)return;
  addChat("user",msg); input.value=""; const reply=addChat("assistant","Thinking…");
  try{
    const r=await fetch("/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:msg,run_id:runId})});
    const d=await readJsonResponse(r); reply.textContent=`${d.provider||"AI"}: ${d.answer||"No answer returned."}`;
  }catch(e){reply.textContent=`Assistant error: ${e.message}`;}
}
document.addEventListener("DOMContentLoaded",()=>{const i=$("chatInput");if(i)i.addEventListener("keydown",e=>{if(e.key==="Enter")sendChat();});});

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
  if(!/^\+[1-9]\d{7,14}$/.test(phone)){showError("Enter a valid E.164 phone number, for example +919876543210.");return}
  const supplierName="Supplier A";
  const btn=$("testCallBtn");
  btn.disabled=true; btn.textContent="CALL REQUESTING...";
  $("callStatus").classList.remove("hidden");
  $("callStatus").innerHTML=`<b>Sending live call request...</b><div class="meta">Supplier: ${esc(supplierName)}</div><div class="meta">Phone: ${maskPhone(phone)}</div><div class="meta">Format: VALID E.164</div>`;
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
        const friendly=category==="supplier_unavailable"?`<div class="bad"><b>Reason:</b> ${esc(reason)}</div><div><b>Telephony code:</b> ${esc(safeText(diagnostics.attempt_failure_code||failureCode))}</div><div><b>Original CALL-E message:</b> ${esc(failureMessage)}</div>`:`<div class="bad"><b>Failure category:</b> ${esc(safeText(category))}<br><b>Reason:</b> ${esc(safeText(reason))}</div>`;
        $("callStatus").innerHTML=html+friendly+diagnosticHtml(failureCode,failureMessage,diagnostics); return;
      }
      if(status==="canceled"){$("callStatus").innerHTML=html+`<div class="bad">The CALL-E call was canceled.</div>`;return}
      if(status==="completed"){
        const result=data.structured_result||data.result||data.recipient_result||null;
        html+=`<div class="good"><b>CALL COMPLETED</b></div>`;
        if(result?.answered) html+=`<div class="call-result"><b>Telephony test response</b><div>Recipient answered: ${esc(safeText(result.answered))}</div></div>`;
        else if(result) {
          const complete=result.available_quantity!=null&&result.unit_price!=null&&result.delivery_hours!=null&&result.currency&&result.can_fulfill&&result.can_fulfill!=="unknown";
          html+=`<div class="call-result"><b>${complete?"Supplier response":"INCOMPLETE OFFER"}</b><div>Available quantity: ${esc(safeText(result.available_quantity))}</div><div>Unit price: ${esc(safeText(result.unit_price))} ${esc(safeText(result.currency))}</div><div>Delivery: ${esc(safeText(result.delivery_hours))} hours</div><div>Fulfillment: ${esc(safeText(result.can_fulfill))}</div></div>`;
        } else html+=`<div class="bad"><b>Structured result:</b> Not available</div>`;
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
scheduleMaintenanceIntelligence();
["part","desc","techs","techOverride","installOverride"].forEach(id=>{
  $(id).addEventListener("input",scheduleMaintenanceIntelligence);
  $(id).addEventListener("change",scheduleMaintenanceIntelligence);
});
