let runId=null;
let chatHistory=[];
let chatBusy=false;
let maintenanceTimer=null;
let lastRunData=null;
let lastAiText=null;
let activityLog=[];
let costTicker=null;
let runStartMs=null;
const $ = id => document.getElementById(id);

function logActivity(text){
  activityLog.push({text, time:new Date()});
  const el=$("activityList");
  if(!el)return;
  el.innerHTML=activityLog.slice().reverse().map(a=>
    `<div class="log-line"><span class="t">${a.time.toLocaleTimeString()}</span>${esc(a.text)}</div>`
  ).join("");
}

function startCostTicker(downtimePerHour){
  clearInterval(costTicker);
  runStartMs=Date.now();
  const el=$("incCost");
  const perSecond=Number(downtimePerHour||0)/3600;
  costTicker=setInterval(()=>{
    const elapsed=(Date.now()-runStartMs)/1000;
    el.textContent=money(elapsed*perSecond);
  },1000);
}

function tickClock(){
  $("clock").textContent=new Date().toLocaleTimeString("en-IN",{hour12:false});
}
setInterval(tickClock,1000); tickClock();

function typeText(el,text,speed=10){
  el.classList.remove("hidden");
  el.innerHTML="";
  const span=document.createElement("span");
  const cursor=document.createElement("span");
  cursor.className="cursor";
  el.appendChild(span); el.appendChild(cursor);
  let i=0;
  (function step(){
    if(i<=text.length){ span.textContent=text.slice(0,i); i++; setTimeout(step,speed); }
    else { cursor.remove(); }
  })();
}
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
function safeText(value){return value===undefined||value===null||value===""?"—":typeof value==="object"?JSON.stringify(value):String(value)}

function renderTechnician(technician){
  const configured=technician.configured===true;
  const el=$("techHint");
  if(!configured){el.textContent="Enter a part number to check technician requirements";return}
  const reqCount=safeText(technician.required_technicians);
  const install=technician.installation_required===true
    ? `${technician.installation_minutes!=null?technician.installation_minutes+"m install":"install required"}`
    : "no install";
  const status=(technician.status||"").toUpperCase();
  el.textContent=`${reqCount} techs needed · ${install} · ${status} (${technician.source||"demo KB"})`;
  el.className="field-hint"+(status==="AVAILABLE"?" ok":status==="SHORTAGE"?" warn":"");
}

async function loadMaintenanceIntelligence(){
  const params=new URLSearchParams({
    part_number:$('part').value.trim(),
    part_description:$('desc').value.trim(),
    available_technicians:$('techs').value||"3"
  });
  if($('techOverride').value) params.set('required_technicians_override',$('techOverride').value);
  if($('installOverride').value) params.set('installation_minutes_override',$('installOverride').value);
  try{
    const response=await fetch(`/api/maintenance/intelligence?${params}`);
    renderTechnician(await readJsonResponse(response));
  }catch(error){
    renderTechnician({configured:false,status:"not configured",source:"Not configured"});
  }
}

function scheduleMaintenanceIntelligence(){
  clearTimeout(maintenanceTimer);
  maintenanceTimer=setTimeout(loadMaintenanceIntelligence,300);
}

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
  $("stage").textContent="CALLING"; $("stage").classList.add("active");
  $("startBtn").disabled=true;

  $("incidentHeader").classList.remove("hidden");
  $("incMachine").textContent=payload.machine||"—";
  $("incPart").textContent=`${payload.part_number} × ${payload.quantity}`;
  $("incQty").textContent=payload.quantity;
  $("incDeadline").textContent=`${payload.max_hours}h`;
  startCostTicker(payload.downtime_cost_per_hour);

  logActivity(`recovery_start machine="${payload.machine}" part="${payload.part_number}" qty=${payload.quantity}`);
  try{
    const r=await fetch("/api/recovery/start",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const data=await readJsonResponse(r);
    runId=data.run_id; render(data);
    chatHistory=[];
    $("chatMessages").innerHTML="";
    appendChatMessage("assistant","Ask me anything about this recovery run — suppliers, offers, the recommended plan, or costs.");
    (data.calls||[]).forEach(c=>logActivity(`live_call supplier="${c.supplier}" status=${c.status} call_id=${c.call_id}`));
    logActivity(`recovery_complete suppliers_called=${(data.calls||[]).length} remaining=${data.remaining_quantity??"?"}`);
    $("replanPanel").classList.toggle("hidden", !(data.remaining_quantity>0 && data.next_supplier_available));
    if(data.remaining_quantity>0 && data.next_supplier_available){
      $("replanMessage").innerHTML=`<b>Shortfall remains: ${esc(data.remaining_quantity)} units.</b> The automatic workflow stopped after the adaptive call cycle. Use this button only if you want to continue to the next configured supplier.`;
    }else if(data.remaining_quantity>0){
      $("replanMessage").innerHTML=`<b>Unresolved shortfall: ${esc(data.remaining_quantity)} units.</b> No additional configured suppliers remain.`;
    }
    $("planPanel").classList.remove("hidden");
  }catch(e){showError(e.message)} finally{$("startBtn").disabled=false; $("stage").classList.remove("active")}
}

async function replan(){
  if(!runId)return;
  clearError(); $("stage").textContent="REPLANNING"; $("stage").classList.add("active");
  $("replanMessage").textContent="Calling the next supplier using the discovered shortfall…";
  try{
    const r=await fetch(`/api/recovery/${runId}/replan`,{method:"POST"});
    const data=await readJsonResponse(r);
    render(data); $("planPanel").classList.remove("hidden");
    logActivity(`adaptive_call offers=${(data.offers||[]).length} remaining=${data.remaining_quantity??"?"}`);
    $("replanPanel").classList.toggle("hidden", !(data.remaining_quantity>0 && data.next_supplier_available));
    $("replanMessage").innerHTML=data.remaining_quantity>0
      ? (data.next_supplier_available ? `<b>Shortfall remains: ${esc(data.remaining_quantity)} units.</b> One more supplier can be called.` : `<b>Unresolved shortfall: ${esc(data.remaining_quantity)} units.</b> No more configured suppliers remain.`)
      : "<b>Quantity fully covered.</b> No further supplier calls are needed.";
  }catch(e){showError(e.message)} finally{$("stage").classList.remove("active")}
}

async function getAiRecommendation(){
  if(!runId)return;
  const btn=$("aiRecBtn"), box=$("aiRecommendation");
  btn.disabled=true; btn.textContent="Asking AI…";
  box.classList.remove("hidden"); box.classList.remove("fallback");
  box.innerHTML='<span class="spark"></span>Analyzing the recovery plan…';
  try{
    const r=await fetch(`/api/recovery/${runId}/recommendation`,{method:"POST"});
    const data=await readJsonResponse(r);
    lastAiText=data.text||"No recommendation returned.";
    if(data.source==="fallback")box.classList.add("fallback");
    typeText(box,lastAiText);
    logActivity(`ai_recommendation source=${data.source}`);
  }catch(e){
    box.textContent="Could not get an AI recommendation: "+e.message;
  }finally{
    btn.disabled=false; btn.textContent="Get AI recommendation";
  }
}

function downloadReport(){
  if(!lastRunData){showError("Start a recovery run first.");return}
  const d=lastRunData, req=d.request||{}, p=d.recommended_plan;
  const lines=[];
  lines.push("RESTARTAI — EMERGENCY RECOVERY REPORT");
  lines.push(`Generated: ${new Date().toLocaleString()}`);
  lines.push(`Run ID: ${d.run_id}`);
  lines.push("");
  lines.push(`Machine: ${req.machine||""}`);
  lines.push(`Part: ${req.part_number||""} — ${req.part_description||""}`);
  lines.push(`Quantity required: ${req.quantity||""}`);
  lines.push(`Max recovery time: ${req.max_hours||""}h`);
  lines.push(`Downtime cost: ₹${req.downtime_cost_per_hour||0}/hour`);
  lines.push("");
  lines.push("SUPPLIER OFFERS");
  (d.offers||[]).forEach(o=>lines.push(`- ${o.supplier}: ${o.status}, ${o.quantity_available} units @ ₹${o.unit_price}, ${o.availability_hours}h, ${o.delivery_method}${o.compatibility_confidence!=null?`, ${Math.round(o.compatibility_confidence*100)}% match`:""}`));
  lines.push("");
  if(p){
    lines.push("RECOMMENDED PLAN");
    (p.legs||[]).forEach(l=>lines.push(`- ${l.supplier}: ${l.quantity} units @ ₹${l.unit_price}, arrival ${l.arrival_hours}h`));
    lines.push(`Total purchase cost: ₹${p.total_purchase_cost}`);
    lines.push(`Material arrival: ${p.material_arrival_hours}h · Installation: ${p.installation_minutes} min`);
    lines.push(`Recovery time: ${p.recovery_time_hours}h`);
    lines.push(`Downtime exposure: ₹${p.downtime_exposure}`);
    lines.push(`Total economic exposure: ₹${p.total_exposure}`);
    lines.push(`Feasible: ${p.feasible?"Yes":"No"}`);
    if(p.infeasibility_reasons&&p.infeasibility_reasons.length) lines.push(`Reasons: ${p.infeasibility_reasons.join("; ")}`);
  }
  if((d.plans||[]).length>1){
    lines.push("");
    lines.push("ALTERNATIVE PLANS CONSIDERED");
    d.plans.slice(1,4).forEach((alt,i)=>lines.push(`#${i+2}: ${(alt.legs||[]).map(l=>`${l.supplier} (${l.quantity})`).join(" + ")} — ₹${alt.total_exposure} total, ${alt.recovery_time_hours}h, ${alt.feasible?"feasible":"not feasible"}`));
  }
  if(lastAiText){
    lines.push("");
    lines.push("AI RECOMMENDATION");
    lines.push(lastAiText);
  }
  lines.push("");
  lines.push(`Approved: ${d.approved?"Yes — human operator approved this plan.":"No — pending human approval. No purchase has been executed."}`);
  const blob=new Blob([lines.join("\n")],{type:"text/plain"});
  const url=URL.createObjectURL(blob);
  const a=document.createElement("a");
  a.href=url; a.download=`restartai-report-${d.run_id}.txt`;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
  logActivity("Recovery report downloaded");
}

async function approve(){
  if(!runId)return;
  clearError();
  try{
    const r=await fetch(`/api/recovery/${runId}/approve`,{method:"POST"});
    const data=await readJsonResponse(r);
    render(data); $("stage").textContent="APPROVED";
    logActivity("plan_approved by=human_operator purchase=not_automated");
  }catch(e){showError(e.message)}
}

function renderAlternatives(plans){
  const el=$("altPlans");
  if(!el)return;
  const alts=(plans||[]).slice(1,4);
  if(!alts.length){el.innerHTML='<tr class="empty-row"><td colspan="3">No alternative feasible plan combinations were found.</td></tr>';return}
  el.innerHTML=alts.map((p,i)=>`
    <tr>
      <td>#${i+2}</td>
      <td class="combo">${(p.legs||[]).map(l=>`${esc(l.supplier)} (${l.quantity})`).join(" + ")}</td>
      <td>${money(p.total_exposure)} · ${p.recovery_time_hours}h · ${p.feasible?"feasible":"not feasible"}</td>
    </tr>`).join("");
}

function renderCallTimeline(calls){
  const el=$("callTimeline");
  if(!el)return;
  if(!calls.length){el.innerHTML='<div class="empty-row">No recovery calls yet.</div>';return}
  el.innerHTML=calls.map(c=>{
    const r=c.structured_result||{};
    const hasResult=Object.keys(r).length>0;
    const status=String(c.status||"unknown").toUpperCase();
    const result=hasResult?`<div class="call-result-grid">
      <div><small>Available</small><b>${esc(safeText(r.available_quantity))}</b></div>
      <div><small>Unit price</small><b>${esc(safeText(r.unit_price))} ${esc(safeText(r.currency||"INR"))}</b></div>
      <div><small>Delivery</small><b>${esc(safeText(r.delivery_hours))}h</b></div>
      <div><small>Compatibility</small><b>${esc(safeText(r.compatible))}</b></div>
    </div>`:"";
    const summary=c.summary||r.summary||c.human_message||"";
    return `<div class="call-event">
      <div class="call-event-head"><b>${esc(c.supplier||"Supplier")}</b><span class="stage-badge">${esc(status)}</span></div>
      <div class="call-meta"><span>${esc(c.phone_masked||"")}</span><span class="call-id">${esc(c.call_id||"")}</span><span>${c.transcript_available?"Transcript captured":"No transcript"}</span></div>
      ${summary?`<div class="call-summary">${esc(summary)}</div>`:""}
      ${result}
      ${c.failure_message?`<div class="bad call-summary">${esc(c.failure_message)}</div>`:""}
    </div>`;
  }).join("");
}

function render(data){
  lastRunData=data;
  $("stage").textContent=(data.stage||"").toUpperCase();

  const offers=data.offers||[];
  const confirmedOffers=offers.filter(o=>o.compatible&&o.confirmed&&Number(o.quantity_available)>0);
  if(!offers.length){
    $("offerRows").innerHTML='<tr class="empty-row"><td colspan="7">Start a recovery run to see supplier calls.</td></tr>';
    $("offerNotes").innerHTML="";
  }else{
    $("offerRows").innerHTML=offers.map(o=>{
      const statusClass=/full/i.test(o.status)?"full":/partial/i.test(o.status)?"partial":"none";
      const match=o.compatibility_confidence!=null?`<span class="match-badge">${Math.round(o.compatibility_confidence*100)}%</span>`:"—";
      return `<tr>
        <td><span class="status-dot ${statusClass}"></span>${esc(o.supplier)}</td>
        <td>${esc(o.status)}</td>
        <td>${match}</td>
        <td class="stock-cell">${o.quantity_available}</td>
        <td>${money(o.unit_price)}</td>
        <td>${o.availability_hours}h</td>
        <td>${esc(o.delivery_method)}</td>
      </tr>`;
    }).join("");
    $("offerNotes").innerHTML=(!confirmedOffers.length?`<div class="note-line bad">No supplier has confirmed availability yet.</div>`:"")
      +offers.filter(o=>o.notes).map(o=>`<div class="note-line">${esc(o.supplier)}: ${esc(o.notes)}${o.call_id?` · CALL-E ${esc(o.call_id)}`:""}</div>`).join("");
  }

  renderCallTimeline(data.calls||[]);

  if(data.maintenance){
    const technician=data.technician_intelligence||data.maintenance.technician_intelligence||{
      required:null,
      installation_required:data.maintenance.installation_required,
      installation_minutes:data.maintenance.installation_minutes,
      technicians_available:data.maintenance.technicians_available??data.request?.available_technicians,
      status:null
    };
    renderTechnician(technician.required_technician===undefined?{
      configured:Boolean(technician.required), required_technician:technician.required,
      required_technicians:technician.required_count, installation_required:technician.installation_required,
      installation_minutes:technician.installation_minutes, technicians_available:technician.technicians_available,
      status:technician.status, source:technician.source
    }:technician);
  }
  const p=data.recommended_plan;
  if(p){
    const planReasons=p.infeasibility_reasons?.length
      ? `<div class="plan-why bad">${p.infeasibility_reasons.map(esc).join("<br>")}</div>`
      : `<div class="plan-why good">This plan meets the quantity, technician, and maximum recovery-time constraints.</div>`;
    $("plan").innerHTML=`
      <div class="plan-card ${p.feasible?"":"not-feasible"}">
        <h3>${p.feasible?"Recommended recovery plan":"No feasible recovery plan"}</h3>
        <div class="plan-metrics">
          <div class="metric"><small>RECOVERY</small><b>${p.recovery_time_hours}h</b></div>
          <div class="metric"><small>PURCHASE</small><b>${money(p.total_purchase_cost)}</b></div>
          <div class="metric"><small>DOWNTIME</small><b>${money(p.downtime_exposure)}</b></div>
          <div class="metric"><small>TOTAL EXPOSURE</small><b>${money(p.total_exposure)}</b></div>
        </div>
        ${(p.legs||[]).map(l=>`<div class="leg"><span>${esc(l.supplier)} · ${l.quantity} units${l.compatibility_confidence!=null?` (${Math.round(l.compatibility_confidence*100)}% match)`:""}</span><span>${money(l.unit_price)}/unit · ${l.arrival_hours}h</span></div>`).join("")}
        <div class="plan-foot">Material arrival ${p.material_arrival_hours}h · Installation ${p.installation_minutes}min · Technicians ${p.required_technicians}/${p.available_technicians}</div>
        ${planReasons}
      </div>`;
    renderAlternatives(data.plans);
    $("approveBtn").disabled=!p.feasible || data.approved;
    $("approval").className=data.approved?"approval good":"approval";
    $("approval").textContent=data.approved?"Approved by human operator. No automated purchase was executed.":"No purchase initiated — approval is required.";
  }
}
async function loadConfig(){
  try{
    const r=await fetch("/api/config"); const c=await readJsonResponse(r);
    $("mode").value=c.mode;
    updateModeUI();
    loadMaintenanceIntelligence();
  }catch{}
}
function updateModeUI(){
  const live=$("mode").value==="live";
  $("modePill").textContent=live?"MODE · LIVE CALL-E":"MODE · DEMO";
  $("modePill").classList.toggle("live",live);
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
        const hasData=result && Object.values(result).some(v=>v!==null && v!==undefined && v!=="" && v!=="unknown");
        if(hasData){
          html+=`<div class="call-result"><b>Supplier response</b><div>Available quantity: ${esc(safeText(result.available_quantity))}</div><div>Unit price: ${esc(safeText(result.unit_price))} ${esc(safeText(result.currency))}</div><div>Delivery: ${esc(safeText(result.delivery_hours))} hours</div><div>Fulfillment: ${esc(safeText(result.can_fulfill))}</div></div>`;
        }else{
          html+=`<div class="call-result"><b>Connectivity confirmed</b><div class="note-line">This was a generic connectivity test (no real part was asked about), so there's no quantity/price to report. Use START RECOVERY with your actual part details for a real supplier response.</div></div>`;
        }
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
["part","desc","techs","techOverride","installOverride"].forEach(id=>{
  $(id).addEventListener("input",scheduleMaintenanceIntelligence);
  $(id).addEventListener("change",scheduleMaintenanceIntelligence);
});

function toggleChat(){
  $("chatWidget").classList.toggle("collapsed");
  if(!$("chatWidget").classList.contains("collapsed")) $("chatInput").focus();
}
function appendChatMessage(role,text){
  const el=document.createElement("div");
  el.className=`chat-msg ${role}`;
  el.textContent=text;
  $("chatMessages").appendChild(el);
  $("chatMessages").scrollTop=$("chatMessages").scrollHeight;
  return el;
}
async function submitChat(evt){
  evt.preventDefault();
  if(chatBusy) return false;
  const input=$("chatInput");
  const message=input.value.trim();
  if(!message) return false;
  if(!runId){
    appendChatMessage("assistant","Start a recovery run first so there's data for me to answer from.");
    return false;
  }
  input.value="";
  appendChatMessage("user",message);
  const pending=appendChatMessage("assistant","Thinking...");
  pending.classList.add("pending");
  chatBusy=true;
  $("chatSendBtn").disabled=true;
  try{
    const r=await fetch(`/api/recovery/${runId}/chat`,{
      method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({message,history:chatHistory})
    });
    const data=await readJsonResponse(r);
    pending.remove();
    if(data.success===false){
      appendChatMessage("assistant error",data.error||"The chat assistant hit an error.");
    }else{
      appendChatMessage("assistant",data.text||"(no response)");
      chatHistory.push({role:"user",content:message});
      chatHistory.push({role:"assistant",content:data.text||""});
      chatHistory=chatHistory.slice(-20);
    }
  }catch(e){
    pending.remove();
    appendChatMessage("assistant error",e.message||"Unable to reach the chat assistant.");
  }finally{
    chatBusy=false;
    $("chatSendBtn").disabled=false;
  }
  return false;
}
