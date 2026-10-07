"use strict";
let supportAccessRows=[],supportAccessRequestKey=null;
let supportAccessTimer=null,supportAccessBusy=false,supportAccessRecovering=false,supportAccessGeneration=0;
let supportAccessRenderedKey=null;
function supportAccessStatus(s){return ({pending:"Wacht op Werkstuur",accepted:"Klantcode nodig",approved:"Tijdelijke inzage actief",other_session:"Actief in andere sessie",locked:"Code geblokkeerd",rejected:"Afgewezen",revoked:"Ingetrokken",cancelled:"Beëindigd",expired:"Verlopen",invalidated:"Toegang beëindigd"})[s]||"Geen toegang"}
function supportOrgAction(org,active=false){
 const id=Number(org.id);
 if(org.can_view)return `<button class="btn" type="button" onclick="switchOrganization(${id})" ${active?'aria-current="page"':''}>${active?"Terug naar omgeving":"Open omgeving"}</button>`;
 if(supportAccessRows.some(r=>Number(r.organization_id)===id&&["pending","accepted","approved","other_session"].includes(r.effective_status)))return '<button class="btn" type="button" onclick="show(\'support\')">Supportverzoek bekijken</button>';
 return '<span class="sub">'+(["suspended","archived"].includes(org.status)?"Omgeving niet actief":org.can_request_access?"Klant vraagt support aan":"Eigen bedrijfsbeheerder nodig")+'</span>';
}
function supportOrgSummary(org){return org.can_view?`${Number(org.active_users||0)} teamleden · ${Number(org.cases||0)} dossiers`:"Inzage na klantverzoek en code"}
function openSupportAccessRequest(){
 if(me?.is_platform_owner){show("support");return}
 if(me?.role!=="admin"||!caseWriteAllowed())return;
 if(typeof wsConfirmLeave==="function"&&!wsConfirmLeave())return;
 supportAccessRequestKey=crypto.randomUUID();
 $("accessRequestCompany").textContent="Support aanvragen";$("accessRequestReason").value="";$("accessRequestDuration").value="30";
 $("accessRequestConsent").checked=false;$("accessRequestSubmit").disabled=true;$("accessRequestMessage").textContent="";
 $("supportAccessRequestForm").classList.remove("hidden");
 if(typeof wsResetScope==="function")wsResetScope("supportAccessRequestForm");
 $("supportAccessRequestForm").scrollIntoView({behavior:"smooth",block:"center"});$("accessRequestReason").focus();
}
function closeSupportAccessRequest(){
 if(typeof wsConfirmLeave==="function"&&!wsConfirmLeave())return;
 $("supportAccessRequestForm")?.classList.add("hidden");supportAccessRequestKey=null;
 $("accessRequestReason").value="";$("accessRequestConsent").checked=false;
 if(typeof wsResetScope==="function")wsResetScope("supportAccessRequestForm");
}
async function submitSupportAccessRequest(){
 if(me?.is_platform_owner||me?.role!=="admin"||!caseWriteAllowed()||!$("accessRequestConsent").checked)return;
 const reason=$("accessRequestReason").value.trim();
 if(reason.length<10||reason.length>1000)return wsFieldError("accessRequestReason","Beschrijf het probleem in 10 tot 1000 tekens.","accessRequestMessage");
 if(!supportAccessRequestKey)supportAccessRequestKey=crypto.randomUUID();
 await api("/api/support-access",{method:"POST",body:JSON.stringify({reason,duration_minutes:Number($("accessRequestDuration").value),request_key:supportAccessRequestKey,consent:true})});
 $("accessRequestReason").value="";$("accessRequestConsent").checked=false;$("supportAccessRequestForm").classList.add("hidden");supportAccessRequestKey=null;
 if(typeof wsResetScope==="function")wsResetScope("supportAccessRequestForm");
 await loadSupportAccess();toast("Support aangevraagd. Na accordering krijg je hier een eenmalige klantcode.","good");
}
function supportAccessCard(r,owner){
 const id=Number(r.id),state=r.effective_status,admin=!owner&&me?.role==="admin"&&caseWriteAllowed();
 const status=`<span class="pill ${state==="approved"?"good":["pending","accepted"].includes(state)?"warn":"review"}">${escapeReport(supportAccessStatus(state))}</span>`;
 let action="",code="";
 if(owner&&state==="pending")action=`<div class="field"><label for="accessDuration${id}">Maximale inzageduur na de klantcode</label><select id="accessDuration${id}">${[15,30,60].filter(n=>n<=r.requested_minutes).map(n=>`<option value="${n}" ${n===r.requested_minutes?"selected":""}>${n} minuten</option>`).join("")}</select></div><button class="btn primary" type="button" onclick="decideSupportAccess(${id},'accept')">Verzoek accorderen</button><button class="btn" type="button" onclick="decideSupportAccess(${id},'reject')">Afwijzen</button>`;
 if(owner&&state==="accepted")action=`<div class="field"><label for="accessCode${id}">Eenmalige code van de klant</label><input id="accessCode${id}" type="text" inputmode="numeric" autocomplete="off" maxlength="11" placeholder="1234 5678"></div><button class="btn primary" type="button" onclick="activateSupportAccess(${id})">Code controleren en meekijken</button>`;
 if(owner&&state==="approved")action=`<button class="btn primary" type="button" onclick="switchOrganization(${Number(r.organization_id)})">Omgeving openen</button>`;
 if(owner&&["pending","accepted","approved","other_session"].includes(state))action+=`<button class="btn" type="button" onclick="decideSupportAccess(${id},'cancel')">${state==="pending"?"Verzoek annuleren":"Support beëindigen"}</button>`;
 if(admin&&["pending","accepted"].includes(state))action=`<button class="btn danger" type="button" onclick="decideSupportAccess(${id},'cancel')">Verzoek intrekken</button>`;
 if(admin&&state==="approved")action=`<button class="btn danger" type="button" onclick="decideSupportAccess(${id},'revoke')">Toegang direct intrekken</button>`;
 if(admin&&state==="accepted")code=r.code?`<div class="cmd-support-code"><span>Jouw eenmalige klantcode</span><strong aria-label="Klantcode">${escapeReport(r.code.slice(0,4))} ${escapeReport(r.code.slice(4))}</strong><p>Geef deze code rechtstreeks aan ${escapeReport(r.requester_name)} wanneer je samen contact hebt. De code staat alleen op jouw scherm en is geldig tot ${escapeReport(fmtDateTime(r.code_expires_at))}. Deel hem alleen als je nu wilt laten meekijken.</p></div>`:`<p class="sub">Alleen ${escapeReport(r.initiated_by_name||"de aanvragende bedrijfsbeheerder")} krijgt de eenmalige klantcode.</p>`;
 const when=state==="approved"||state==="other_session"?`Alleen-lezen inzage tot ${escapeReport(fmtDateTime(r.expires_at))}.`:state==="accepted"?`Geaccordeerd voor ${Number(r.approved_minutes)} minuten. Inzage start pas na de klantcode; de code vervalt om ${escapeReport(fmtDateTime(r.code_expires_at))}.`:state==="pending"?`Klant vraagt ${Number(r.requested_minutes)} minuten inzage. Accordeer vóór ${escapeReport(fmtDateTime(r.request_expires_at))}.`:`Verzoek beëindigd of verlopen. De bedrijfsbeheerder kan opnieuw support aanvragen.`;
 return `<article class="cmd-support-card cmd-access-card" id="accessCard${id}"><div class="cmd-support-top"><span>${owner?escapeReport(r.organization_name):"Werkstuur support: "+escapeReport(r.requester_name)}</span>${status}</div><h3>Supportverzoek van ${escapeReport(r.initiated_by_name||"de bedrijfsbeheerder")}</h3><p class="cmd-support-description">${escapeReport(r.reason)}</p><p class="sub">${when}</p><p class="cmd-access-scope">Tijdelijk alleen-lezen inzage in dossiers, bijlagen, klanten, team en resultaten. Geen wijzigingen, accountovername, volledige exports of back-updownloads. De bedrijfsbeheerder kan direct intrekken.</p>${code}<div class="cmd-access-actions">${action}</div></article>`;
}
async function loadSupportAccess(){
 const owner=!!me?.is_platform_owner,admin=me?.role==="admin"&&!owner&&caseWriteAllowed(),org=me?.organization_id;
 $("supportAccessPanel")?.classList.toggle("hidden",!owner&&!admin);$("supportAccessRequestButton")?.classList.toggle("hidden",!admin);
 if(!owner&&!admin){$("supportAccessBadge")?.classList.add("hidden");return}
 const data=await api(owner?"/api/owner/support-access":"/api/support-access");
 if(!me||me.organization_id!==org)return;
 supportAccessRows=data.requests;
 const pending=data.requests.filter(r=>["pending","accepted"].includes(r.effective_status)).length;
 $("supportAccessBadge").textContent=String(pending);$("supportAccessBadge").classList.toggle("hidden",pending===0);
 $("supportAccessTitle").textContent=owner?"Klantverzoeken en toegangscodes":"Support aanvragen en inzage beheren";
 const renderKey=JSON.stringify([org,me.id,owner,data.requests]);
 if(renderKey!==supportAccessRenderedKey){
  $("supportAccessList").innerHTML=data.requests.length?data.requests.map(r=>supportAccessCard(r,owner)).join(""):emptyState("Geen supportverzoeken",owner?"De klant vraagt support aan. Jij accordeert het verzoek en voert daarna de eenmalige code van de klant in.":"Klik op Support aanvragen. Na accordering door Werkstuur krijg je een eenmalige code waarmee je de inzage kunt laten beginnen.","shield");
  supportAccessRenderedKey=renderKey;
 }
 const labels={requested:"Support door klant aangevraagd",accepted:"Verzoek door Werkstuur geaccordeerd",activated:"Klantcode gecontroleerd",code_failed:"Verkeerde code ingevoerd",locked:"Code na vijf pogingen geblokkeerd",approved:"Oude toestemming",rejected:"Verzoek afgewezen",revoked:"Toegang ingetrokken",cancelled:"Support beëindigd",expired:"Verzoek of toegang verlopen",opened:"Werkomgeving geopend",left:"Werkomgeving verlaten"};
 $("supportAccessHistory").innerHTML=data.events.length?'<h3>Geschiedenis</h3>'+data.events.slice(0,40).map(e=>`<div class="cmd-access-event"><time>${escapeReport(fmtDateTime(e.created_at))}</time><span>${escapeReport(e.actor_name)} · ${escapeReport(labels[e.action]||e.action)}</span></div>`).join(""):"";
}
async function decideSupportAccess(id,action){
 const r=supportAccessRows.find(r=>Number(r.id)===Number(id));if(!r)return;
 const owner=!!me?.is_platform_owner,payload={action,version:r.version};
 if(action==="accept")payload.duration_minutes=Number($("accessDuration"+id).value);
 try{await api(`${owner?"/api/owner/support-access/":"/api/support-access/"}${Number(id)}`,{method:"POST",body:JSON.stringify(payload)})}
 catch(error){await loadSupportAccess();throw error}
 if(owner&&action==="cancel"&&Number(me?.organization_id)===Number(r.organization_id))await recoverSupportAccess();
 await loadSupportAccess();toast(action==="accept"?"Geaccordeerd. De klant krijgt nu de code; je hebt nog geen inzage.":action==="revoke"?"Toegang direct ingetrokken.":action==="reject"?"Verzoek afgewezen.":"Supportverzoek beëindigd.","good");
}
async function activateSupportAccess(id){
 const r=supportAccessRows.find(r=>Number(r.id)===Number(id));if(!r||!me?.is_platform_owner)return;
 const code=$("accessCode"+id).value.replace(/[ -]/g,"");
 if(!/^[0-9]{8}$/.test(code)){toast("Vul de acht cijfers van de klantcode in.","warn");return}
 try{await api(`/api/owner/support-access/${Number(id)}/activate`,{method:"POST",body:JSON.stringify({code,version:r.version})})}
 catch(error){$("accessCode"+id).value="";await loadSupportAccess();throw error}
 $("accessCode"+id).value="";await loadOwnerConsole();await switchOrganization(Number(r.organization_id));
}
function clearSupportWorkspace(){
 supportAccessGeneration++;current=null;cases=[];users=[];settings=null;resetCustomerWorkspace();
 for(const id of ["dashboardView","casesView","caseView","customersView","teamView","metricsView","pilotHubView","pilotView","reportView","auditView","productView","settingsView","onboardingView","systemView","beheerView"]){$(id)?.classList.add("hidden")}
 for(const selector of ["#caseView input","#caseView textarea","#caseView select","#supportAccessList input"]){document.querySelectorAll(selector).forEach(el=>{el.value="";el.disabled=true})}
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
  }else if(me.is_platform_owner||me.role==="admin"){await loadSupportAccess()}
 }catch(error){if(me?.is_platform_owner&&!caseWriteAllowed()){clearSupportWorkspace();toast("Toestemming kon niet worden gecontroleerd. Inzage is tijdelijk gesloten.","warn")}}
 finally{supportAccessBusy=false}
}
function initSupportAccessMonitoring(){
 if(supportAccessTimer)clearInterval(supportAccessTimer);
 supportAccessTimer=setInterval(checkSupportAccess,15000);renderSupportAccessBanner();
 if(me?.is_platform_owner||me?.role==="admin")loadSupportAccess().catch(()=>{});
}
if(typeof wsProtectedScopes!=="undefined")wsProtectedScopes.push("supportAccessRequestForm");
for(const [name,fn] of Object.entries({submitSupportAccessRequest,decideSupportAccess,activateSupportAccess,recoverSupportAccess}))window[name]=(...args)=>runWorkspaceAction(name,fn,args);
const loadSupportBeforeAccess=loadSupport;
loadSupport=async function(){await Promise.all([loadSupportBeforeAccess(),loadSupportAccess()])};
const configureRoleBeforeAccess=configureRoleNavigation;
configureRoleNavigation=function(view){configureRoleBeforeAccess(view);renderSupportAccessBanner()};
document.addEventListener("visibilitychange",()=>{if(!document.hidden)checkSupportAccess()});
window.addEventListener("focus",checkSupportAccess);
