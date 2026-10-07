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
 loadSupport:async()=>{},configureRoleNavigation:()=>{},show:()=>{},resetCustomerWorkspace:()=>{},
 document:{hidden:false,querySelectorAll:()=>[],addEventListener:()=>{}},window:{addEventListener:()=>{}},setInterval:()=>1,clearInterval:()=>{}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync("support-access.js","utf8"),ctx);
const request={id:1,organization_id:2,requester_name:'TEST Owner <script>',reason:'TEST reason <script>alert(1)</script>',requested_minutes:30,effective_status:"pending",version:1,request_expires_at:"tomorrow",organization_name:"TEST Bedrijf"};
async function check(name,fn){await fn();passed++;process.stdout.write("PASS "+name+"\n")}
(async()=>{
 await check("Unapproved environment offers a request and no Open action",()=>{const html=ctx.supportOrgAction({id:2,can_view:false,can_request_access:true});assert(html.includes("Toestemming aanvragen"));assert(!html.includes("switchOrganization"))});
 await check("Missing administrator and paused company explain why requests are unavailable",()=>{assert(ctx.supportOrgAction({id:2,can_view:false,can_request_access:false}).includes("bedrijfsbeheerder"));assert(ctx.supportOrgAction({id:2,can_view:false,can_request_access:false,status:"suspended"}).includes("niet actief"))});
 await check("Pending request links to permission overview without granting access",()=>{vm.runInContext('supportAccessRows=[{organization_id:2,effective_status:"pending"}]',ctx);assert(ctx.supportOrgAction({id:2,can_view:false,can_request_access:true}).includes("Verzoek bekijken"))});
 await check("Approved and home environments have a context action",()=>assert(ctx.supportOrgAction({id:2,can_view:true},true).includes("Terug naar omgeving")));
 await check("Company approval requires an explicit checkbox and starts disabled",()=>{const html=ctx.supportAccessCard(request,false);assert(html.includes('type="checkbox"'));assert(html.includes('disabled onclick="decideSupportAccess(1,\'approve\')"'));assert(!html.includes('value="60"'));assert(html.includes("Afwijzen"))});
 await check("Request reason and personal display name cannot inject HTML",()=>{const html=ctx.supportAccessCard(request,false);assert(!html.includes("<script>"));assert(html.includes("&lt;script&gt;"))});
 await check("Owner cannot see a self-approval button",()=>{const html=ctx.supportAccessCard(request,true);assert(!html.includes("Toestemming geven"));assert(!html.includes("Omgeving openen"));assert(html.includes("Verzoek annuleren"))});
 await check("Technician has no approval controls",()=>{ctx.me.role="technician";assert(!ctx.supportAccessCard(request,false).includes("Toestemming geven"));ctx.me.role="admin"});
 await check("Active company approval has immediate revoke; revoked has no Open",()=>{assert(ctx.supportAccessCard({...request,effective_status:"approved"},false).includes("direct intrekken"));assert(!ctx.supportAccessCard({...request,effective_status:"revoked"},true).includes("Omgeving openen"))});
 rows=[request];await ctx.loadSupportAccess();
 await check("Unchecked approval makes no API call",async()=>{calls.length=0;await ctx.decideSupportAccess(1,"approve");assert.equal(calls.length,0)});
 await check("Checked approval sends the selected shorter duration and version",async()=>{node("accessConfirm1").checked=true;node("accessDuration1").value="15";calls.length=0;await ctx.decideSupportAccess(1,"approve");assert.equal(calls[0][0],"/api/support-access/1");assert.deepEqual(JSON.parse(calls[0][1].body),{action:"approve",version:1,duration_minutes:15})});
 await check("Unchanged refresh preserves approval selection and checkbox markup",async()=>{node("supportAccessList").innerHTML="retained pending form";await ctx.loadSupportAccess();assert.equal(node("supportAccessList").innerHTML,"retained pending form")});
 await check("Non-admin company user never requests the consent API",async()=>{ctx.me.role="planner";calls.length=0;await ctx.loadSupportAccess();assert.equal(calls.length,0);assert(node("supportAccessPanel").classes.has("hidden"));ctx.me.role="admin"});
 await check("Clearing support removes cached people, cases and visible workspaces",()=>{ctx.cases=[{private:true}];ctx.current={private:true};ctx.users=[{private:true}];ctx.clearSupportWorkspace();assert.equal(ctx.cases.length,0);assert.equal(ctx.users.length,0);assert.equal(ctx.current,null);assert(node("caseView").classes.has("hidden"))});
 process.stdout.write(JSON.stringify({passed,total:passed,mode:"actual consent UI functions, isolated DOM and API"})+"\n");
})().catch(error=>{console.error(error);process.exitCode=1});
