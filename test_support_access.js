"use strict";
const fs=require("fs"),vm=require("vm"),assert=require("assert");
let passed=0;
const nodes=new Map();
function node(id){if(!nodes.has(id))nodes.set(id,{value:"",textContent:"",innerHTML:"",checked:false,disabled:false,classes:new Set(),classList:{add(n){nodes.get(id).classes.add(n)},remove(n){nodes.get(id).classes.delete(n)},toggle(n,on){if(on)nodes.get(id).classes.add(n);else nodes.get(id).classes.delete(n)}},focus(){},scrollIntoView(){}});return nodes.get(id)}
const calls=[],warnings=[];
let rows=[];
const ctx={console,me:{id:2,role:"admin",is_platform_owner:false,organization_id:2,base_organization_id:2,organization_name:"TEST Bedrijf"},settings:{},ownerOrganizations:[],$:node,
 caseWriteAllowed:()=>ctx.me.organization_id===ctx.me.base_organization_id,
 escapeReport:v=>String(v??"").replace(/[<>&"']/g,c=>({"<":"&lt;",">":"&gt;","&":"&amp;",'"':"&quot;","'":"&#39;"})[c]),
 fmtDateTime:v=>String(v),emptyState:(title,description)=>title+description,
 api:async(path,opts={})=>{calls.push([path,opts]);return {requests:rows,events:[]}},
 toast:(...args)=>warnings.push(args),runWorkspaceAction:(name,fn,args)=>fn(...args),wsProtectedScopes:[],
 loadSupport:async()=>{},loadOwnerConsole:async()=>{},switchOrganization:async()=>{},crypto:{randomUUID:()=>"isolated-client-request-key-123"},configureRoleNavigation:()=>{},show:()=>{},resetCustomerWorkspace:()=>{},
 document:{hidden:false,querySelectorAll:()=>[],addEventListener:()=>{}},window:{addEventListener:()=>{}},setInterval:()=>1,clearInterval:()=>{}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync("support-access.js","utf8"),ctx);

const request={id:1,organization_id:2,requester_name:'TEST Owner <script>',initiated_by_name:'TEST Admin <script>',initiated_by:2,reason:'TEST reason <script>alert(1)</script>',requested_minutes:30,effective_status:"pending",version:1,request_expires_at:"tomorrow",organization_name:"TEST Bedrijf"};
async function check(name,fn){await fn();passed++;process.stdout.write("PASS "+name+"\n")}
(async()=>{
 await check("Owner cannot start an access request or open an unapproved company",()=>{const html=ctx.supportOrgAction({id:2,can_view:false,can_request_access:true});assert(html.includes("Klant vraagt support aan"));assert(!html.includes("switchOrganization"));assert(!html.includes("Toestemming aanvragen"))});
 await check("Paused and unconfigured companies stay closed",()=>{assert(ctx.supportOrgAction({id:2,can_view:false,can_request_access:false}).includes("bedrijfsbeheerder"));assert(ctx.supportOrgAction({id:2,can_view:false,status:"suspended"}).includes("niet actief"))});
 await check("Pending requests lead owner to the support notification",()=>{vm.runInContext('supportAccessRows=[{organization_id:2,effective_status:"pending"}]',ctx);assert(ctx.supportOrgAction({id:2,can_view:false}).includes("Supportverzoek bekijken"))});
 await check("Home and approved context actions remain available",()=>assert(ctx.supportOrgAction({id:2,can_view:true},true).includes("Terug naar omgeving")));
 await check("Owner acceptance has shorter duration choices and gives no Open action",()=>{const html=ctx.supportAccessCard(request,true);assert(html.includes("Verzoek accorderen"));assert(!html.includes("Omgeving openen"));assert(!html.includes('value="60"'))});
 await check("Company cannot accorder its own request; it can cancel",()=>{const html=ctx.supportAccessCard(request,false);assert(!html.includes("Verzoek accorderen"));assert(html.includes("Verzoek intrekken"))});
 await check("Names and reasons cannot inject HTML",()=>{const html=ctx.supportAccessCard(request,false);assert(!html.includes("<script>"));assert(html.includes("&lt;script&gt;"))});
 await check("Owner must enter code after acceptance and never sees customer code",()=>{const html=ctx.supportAccessCard({...request,effective_status:"accepted",code:"87654321"},true);assert(html.includes("Code controleren en meekijken"));assert(!html.includes("8765 4321"));assert(!html.includes("Omgeving openen"))});
 await check("Only supplied company code is displayed; other admin is directed to initiator",()=>{assert(ctx.supportAccessCard({...request,effective_status:"accepted",code:"87654321"},false).includes("8765 4321"));assert(ctx.supportAccessCard({...request,effective_status:"accepted"},false).includes("Alleen TEST Admin"))});
 await check("Technician has no approval, cancellation or code controls",()=>{ctx.me.role="technician";const html=ctx.supportAccessCard({...request,effective_status:"accepted"},false);assert(!html.includes("Verzoek intrekken"));assert(!html.includes("accessCode1"));ctx.me.role="admin"});
 await check("Active company can revoke; locked or expired request cannot be opened",()=>{assert(ctx.supportAccessCard({...request,effective_status:"approved"},false).includes("direct intrekken"));for(const state of ["locked","expired","other_session"])assert(!ctx.supportAccessCard({...request,effective_status:state},true).includes("Omgeving openen"))});
 await check("Unchecked customer consent makes no request",async()=>{calls.length=0;node("accessRequestConsent").checked=false;await ctx.submitSupportAccessRequest();assert.equal(calls.length,0)});
 await check("Customer initiation sends consent and duration without any identity or company fields",async()=>{node("accessRequestConsent").checked=true;node("accessRequestReason").value="TEST fictief softwareprobleem";node("accessRequestDuration").value="15";calls.length=0;await ctx.submitSupportAccessRequest();assert.equal(calls[0][0],"/api/support-access");assert.deepEqual(JSON.parse(calls[0][1].body),{reason:"TEST fictief softwareprobleem",duration_minutes:15,request_key:"isolated-client-request-key-123",consent:true})});
 ctx.me={...ctx.me,id:1,is_platform_owner:true,organization_id:1,base_organization_id:1};rows=[request];await ctx.loadSupportAccess();
 await check("Owner accordering sends selected limited duration and version",async()=>{node("accessDuration1").value="15";calls.length=0;await ctx.decideSupportAccess(1,"accept");assert.equal(calls[0][0],"/api/owner/support-access/1");assert.deepEqual(JSON.parse(calls[0][1].body),{action:"accept",version:1,duration_minutes:15})});
 rows=[{...request,effective_status:"accepted",version:2}];await ctx.loadSupportAccess();
 await check("Malformed code is rejected before any API request",async()=>{node("accessCode1").value="123";calls.length=0;await ctx.activateSupportAccess(1);assert.equal(calls.length,0)});
 await check("Valid code travels only in POST body and is erased after activation",async()=>{node("accessCode1").value="9988 7766";calls.length=0;await ctx.activateSupportAccess(1);assert.equal(calls[0][0],"/api/owner/support-access/1/activate");assert.deepEqual(JSON.parse(calls[0][1].body),{code:"99887766",version:2});assert.equal(node("accessCode1").value,"")});
 await check("Refresh of an unchanged request preserves code entry",async()=>{node("supportAccessList").innerHTML="retained code form";node("accessCode1").value="9988";await ctx.loadSupportAccess();assert.equal(node("supportAccessList").innerHTML,"retained code form");assert.equal(node("accessCode1").value,"9988")});
 await check("Owner foreground polling retrieves new client requests",async()=>{calls.length=0;await ctx.checkSupportAccess();assert.equal(calls[0][0],"/api/owner/support-access")});
 await check("Planner cannot fetch the consent API",async()=>{ctx.me={...ctx.me,is_platform_owner:false,role:"planner",organization_id:2,base_organization_id:2};calls.length=0;await ctx.loadSupportAccess();assert.equal(calls.length,0);assert(node("supportAccessPanel").classes.has("hidden"))});
 await check("Closing support clears cached cases, people and private workspaces",()=>{ctx.cases=[{private:true}];ctx.current={private:true};ctx.users=[{private:true}];ctx.clearSupportWorkspace();assert.equal(ctx.cases.length,0);assert.equal(ctx.users.length,0);assert.equal(ctx.current,null);assert(node("caseView").classes.has("hidden"))});
 process.stdout.write(JSON.stringify({passed,total:passed,mode:"actual support-code UI functions with isolated DOM and API"})+"\n");
})().catch(error=>{console.error(error);process.exitCode=1});
