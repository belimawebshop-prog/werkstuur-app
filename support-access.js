"use strict";
let supportAccessRows=[],supportAccessRequestKey=null,supportAccessTarget=null;
let supportAccessTimer=null,supportAccessBusy=false,supportAccessRecovering=false,supportAccessGeneration=0;
let supportAccessRenderedKey=null;
function supportAccessStatus(s){return ({pending:"Wacht op toestemming",approved:"Tijdelijke inzage toegestaan",rejected:"Afgewezen",revoked:"Ingetrokken",cancelled:"Beëindigd door Werkstuur",expired:"Verlopen",invalidated:"Toegang beëindigd"})[s]||"Geen toestemming"}
function supportOrgAction(org,active=false){
 const id=Number(org.id);
 if(org.can_view)return `<button class="btn" type="button" onclick="switchOrganization(${id})" ${active?'aria-current="page"':''}>${active?"Terug naar omgeving":"Open omgeving"}</button>`;
 const pending=supportAccessRows.find(r=>Number(r.organization_id)===id&&r.effective_status==="pending");
 if(pending)return '<button class="btn" type="button" onclick="show(\'support\')">Verzoek bekijken</button>';
 return org.can_request_access?`<button class="btn" type="button" onclick="openSupportAccessRequest(${id})">Toestemming aanvragen</button>`:'<span class="sub">'+(org.status==="suspended"||org.status==="archived"?"Omgeving niet actief":"Eigen bedrijfsbeheerder nodig")+'</span>';
}
function supportOrgSummary(org){return org.can_view?`${Number(org.active_users||0)} teamleden · ${Number(org.cases||0)} dossiers`:"Inzage pas na toestemming"}
function openSupportAccessRequest(id){
 if(!me?.is_platform_owner)return;
 const org=ownerOrganizations.find(o=>Number(o.id)===Number(id));if(!org?.can_request_access)return;
 if(typeof wsConfirmLeave==="function"&&!wsConfirmLeave())return;
 supportAccessTarget=Number(id);supportAccessRequestKey=crypto.randomUUID();
 show("owner",false,true);
 $("accessRequestCompany").textContent=org.name;$("accessRequestReason").value="";$("accessRequestDuration").value="60";$("accessRequestMessage").textContent="";
 $("supportAccessRequestForm").classList.remove("hidden");
 if(typeof wsResetScope==="function")wsResetScope("supportAccessRequestForm");
 $("supportAccessRequestForm").scrollIntoView({behavior:"smooth",block:"center"});$("accessRequestReason").focus();
}
function closeSupportAccessRequest(){
 if(typeof wsConfirmLeave==="function"&&!wsConfirmLeave())return;
 $("supportAccessRequestForm")?.classList.add("hidden");supportAccessTarget=null;supportAccessRequestKey=null;
 if(typeof wsResetScope==="function")wsResetScope("supportAccessRequestForm");
}
async function submitSupportAccessRequest(){
 if(!me?.is_platform_owner||!supportAccessTarget)return;
 const reason=$("accessRequestReason").value.trim();
 if(reason.length<10||reason.length>1000)return wsFieldError("accessRequestReason","Beschrijf de reden in 10 tot 1000 tekens.","accessRequestMessage");
 await api("/api/owner/support-access",{method:"POST",body:JSON.stringify({organization_id:supportAccessTarget,reason,duration_minutes:Number($("accessRequestDuration").value),request_key:supportAccessRequestKey})});
 $("accessRequestReason").value="";$("supportAccessRequestForm").classList.add("hidden");supportAccessTarget=null;supportAccessRequestKey=null;
 if(typeof wsResetScope==="function")wsResetScope("supportAccessRequestForm");
 await loadSupportAccess();await loadOwnerConsole();toast("Verzoek ingediend. De bedrijfsbeheerder beslist in Ondersteuning.","good");
}
function supportAccessCard(r,owner){
 const id=Number(r.id),state=r.effective_status,admin=!owner&&me?.role==="admin"&&caseWriteAllowed();
 const status=`<span class="pill ${state==="approved"?"good":state==="pending"?"warn":"review"}">${escapeReport(supportAccessStatus(state))}</span>`;
 let action="";
 if(owner&&state==="approved")action=`<button class="btn primary" type="button" onclick="switchOrganization(${Number(r.organization_id)})">Omgeving openen</button>`;
 if(owner&&["pending","approved"].includes(state))action+=`<button class="btn" type="button" onclick="decideSupportAccess(${id},'cancel')">${state==="pending"?"Verzoek annuleren":"Toestemming beëindigen"}</button>`;
 if(admin&&state==="pending")action=`<div class="field"><label for="accessDuration${id}">Toegang vanaf jouw goedkeuring</label><select id="accessDuration${id}">${[15,30,60].filter(n=>n<=r.requested_minutes).map(n=>`<option value="${n}" ${n===r.requested_minutes?"selected":""}>${n} minuten</option>`).join("")}</select></div><label class="cmd-access-check"><input type="checkbox" id="accessConfirm${id}" onchange="document.getElementById('accessApprove${id}').disabled=!this.checked">Ik geef ${escapeReport(r.requester_name)} tijdelijk alleen-lezen toegang tot de werkruimte van mijn bedrijf.</label><div class="cmd-access-actions"><button class="btn primary" type="button" id="accessApprove${id}" disabled onclick="decideSupportAccess(${id},'approve')">Toestemming geven</button><button class="btn" type="button" onclick="decideSupportAccess(${id},'reject')">Afwijzen</button></div>`;
 if(admin&&state==="approved")action=`<button class="btn danger" type="button" onclick="decideSupportAccess(${id},'revoke')">Toegang direct intrekken</button>`;
 const when=state==="approved"?`Toegestaan door ${escapeReport(r.decided_by_name||"je bedrijfsbeheerder")} tot ${escapeReport(fmtDateTime(r.expires_at))}.`:state==="pending"?`Aangevraagd voor ${Number(r.requested_minutes)} minuten. Beslis vóór ${escapeReport(fmtDateTime(r.request_expires_at))}.`:`Besluit / einde: ${escapeReport(fmtDateTime(r.ended_at||r.decided_at||r.expires_at||r.request_expires_at))}.`;
 return `<article class="cmd-support-card cmd-access-card" id="accessCard${id}"><div class="cmd-support-top"><span>${owner?escapeReport(r.organization_name):"Aanvrager: "+escapeReport(r.requester_name)}</span>${status}</div><h3>Verzoek om mee te kijken</h3><p class="cmd-support-description">${escapeReport(r.reason)}</p><p class="sub">${when}</p><p class="cmd-access-scope">Inzage in dossiers, bijlagen, klanten, team en resultaten. Geen wijzigingen, accountovername, volledige exports of back-updownloads. Je kunt toestemming direct intrekken.</p><div class="cmd-access-actions">${action}</div></article>`;
}
async function loadSupportAccess(){
 const owner=!!me?.is_platform_owner,admin=me?.role==="admin"&&!owner&&caseWriteAllowed(),org=me?.organization_id;
 $("supportAccessPanel")?.classList.toggle("hidden",!owner&&!admin);
 if(!owner&&!admin){$("supportAccessBadge")?.classList.add("hidden");return}
 const data=await api(owner?"/api/owner/support-access":"/api/support-access");
 if(!me||me.organization_id!==org)return;
 supportAccessRows=data.requests;
 const pending=data.requests.filter(r=>r.effective_status==="pending").length;
 $("supportAccessBadge").textContent=String(pending);$("supportAccessBadge").classList.toggle("hidden",pending===0);
 $("supportAccessTitle").textContent=owner?"Toestemming om mee te kijken":"Jij bepaalt wie mag meekijken";
 const renderKey=JSON.stringify([org,me.id,owner,data.requests]);
 if(renderKey!==supportAccessRenderedKey){
  $("supportAccessList").innerHTML=data.requests.length?data.requests.map(r=>supportAccessCard(r,owner)).join(""):emptyState("Geen inzageverzoeken",owner?"Vraag toestemming aan bij een klantbedrijf. Alleen hun bedrijfsbeheerder kan toegang geven.":"Hier verschijnen verzoeken van Werkstuur om tijdelijk in jouw bedrijfsomgeving mee te kijken.","shield");
  supportAccessRenderedKey=renderKey;
 }
 const labels={requested:"Inzage aangevraagd",approved:"Toestemming gegeven",rejected:"Verzoek afgewezen",revoked:"Toegang ingetrokken",cancelled:"Verzoek of toestemming beëindigd",expired:"Verzoek of toestemming verlopen",opened:"Werkomgeving geopend",left:"Werkomgeving verlaten"};
 $("supportAccessHistory").innerHTML=data.events.length?'<h3>Geschiedenis</h3>'+data.events.slice(0,40).map(e=>`<div class="cmd-access-event"><time>${escapeReport(fmtDateTime(e.created_at))}</time><span>${escapeReport(e.actor_name)} · ${escapeReport(labels[e.action]||e.action)}</span></div>`).join(""):"";
}
async function decideSupportAccess(id,action){
 const r=supportAccessRows.find(r=>Number(r.id)===Number(id));if(!r)return;
 if(action==="approve"&&!$("accessConfirm"+id)?.checked)return;
 const owner=!!me?.is_platform_owner,payload={action,version:r.version};
 if(action==="approve")payload.duration_minutes=Number($("accessDuration"+id).value);
 try{await api(`${owner?"/api/owner/support-access/":"/api/support-access/"}${Number(id)}`,{method:"POST",body:JSON.stringify(payload)})}
 catch(error){await loadSupportAccess();throw error}
 if(owner&&action==="cancel"&&Number(me?.organization_id)===Number(r.organization_id))await recoverSupportAccess();
 await loadSupportAccess();toast(action==="approve"?"Toestemming gegeven. Tijdelijke inzage gaat nu in.":action==="revoke"?"Toegang direct ingetrokken.":action==="reject"?"Verzoek afgewezen.":"Verzoek of toestemming beëindigd.","good");
}
function clearSupportWorkspace(){
 supportAccessGeneration++;current=null;cases=[];users=[];settings=null;resetCustomerWorkspace();
 for(const id of ["dashboardView","casesView","caseView","customersView","teamView","metricsView","pilotHubView","pilotView","reportView","auditView","productView","settingsView","onboardingView","systemView","beheerView"]){$(id)?.classList.add("hidden")}
 for(const selector of ["#caseView input","#caseView textarea","#caseView select"]){document.querySelectorAll(selector).forEach(el=>{el.value="";el.disabled=true})}
 for(const id of ["caseGrid","caseList","caseOverview","noteList","attachmentList","customerList","teamList","accountList","auditList","pilotSnapshots","reportContent"]){if($(id))$(id).innerHTML=""}
 $("newModal")?.classList.add("hidden");
 if(typeof wsResetScope==="function")for(const id of ["caseView","customerEditor","teamCreateForm"])wsResetScope(id);
}
async function recoverSupportAccess(){
 if(supportAccessRecovering||!me?.is_platform_owner)return;
 supportAccessRecovering=true;const base=me.base_organization_id;clearSupportWorkspace();
 try{
  await api("/api/owner/context",{method:"POST",body:JSON.stringify({organization_id:base})});
  me=await api("/api/me");await Promise.all([loadCases(),loadUsers(),loadSettings()]);
  login.classList.add("hidden");app.classList.remove("hidden");
  if(settings)applyBranding(settings);render();updateWorkspaceChrome();show("platform",false,true);
  toast("Supportinzage beëindigd. Je bent terug in Werkstuur Control.","warn");
 }catch(error){showAuthScreen();$("loginMsg").textContent=error.message||"De inzage is gestopt. Vernieuw om je eigen omgeving te openen."}
 finally{supportAccessRecovering=false}
}
function renderSupportAccessBanner(){
 if(!me?.is_platform_owner||caseWriteAllowed())return;
 const grant=me.support_access;
 if($("ownerContextText"))$("ownerContextText").textContent=grant?`Je kijkt alleen-lezen mee bij ${me.organization_name}. Toegestaan door ${grant.approved_by} tot ${fmtDateTime(grant.expires_at)}.`:"Toestemming voor deze inzage is beëindigd.";
}
async function checkSupportAccess(){
 if(supportAccessBusy||document.hidden||!me||supportAccessRecovering)return;
 supportAccessBusy=true;
 try{
  if(me.is_platform_owner&&!caseWriteAllowed()){
   const org=me.organization_id,data=await api("/api/me");
   if(!me||me.organization_id!==org)return;
   if(data.organization_id!==org||!data.support_access){await recoverSupportAccess();return}
   me.support_access=data.support_access;renderSupportAccessBanner();
  }else if(me.role==="admin"&&!me.is_platform_owner){await loadSupportAccess()}
 }catch(error){if(me?.is_platform_owner&&!caseWriteAllowed()){clearSupportWorkspace();toast("Toestemming kon niet worden gecontroleerd. Inzage is tijdelijk gesloten.","warn")}}
 finally{supportAccessBusy=false}
}
function initSupportAccessMonitoring(){
 if(supportAccessTimer)clearInterval(supportAccessTimer);
 supportAccessTimer=setInterval(checkSupportAccess,15000);renderSupportAccessBanner();
 if(me?.role==="admin"&&!me.is_platform_owner)loadSupportAccess().catch(()=>{});
}
if(typeof wsProtectedScopes!=="undefined")wsProtectedScopes.push("supportAccessRequestForm");
for(const [name,fn] of Object.entries({submitSupportAccessRequest,decideSupportAccess,recoverSupportAccess}))window[name]=(...args)=>runWorkspaceAction(name,fn,args);
const loadSupportBeforeAccess=loadSupport;
loadSupport=async function(){await Promise.all([loadSupportBeforeAccess(),loadSupportAccess()])};
const configureRoleBeforeAccess=configureRoleNavigation;
configureRoleNavigation=function(view){configureRoleBeforeAccess(view);renderSupportAccessBanner()};
document.addEventListener("visibilitychange",()=>{if(!document.hidden)checkSupportAccess()});
window.addEventListener("focus",checkSupportAccess);
