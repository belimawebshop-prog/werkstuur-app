// Actual editor helpers with an isolated DOM fixture. No browser or live login.
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const classes=()=>({items:new Set(),contains(name){return this.items.has(name)},toggle(name,on){on?this.items.add(name):this.items.delete(name)}});
class Control{
 constructor(id,type,value=""){this.id=id;this.type=type;this._value=value;this.checked=false;this.files=[];this.attrs={};this.focused=false;this.isConnected=true;this.disabled=false;this.detail={setAttribute(){}}}
 get value(){return this._value}set value(value){this._value=value;if(this.type==="file"&&value==="")this.files=[]}
 setAttribute(k,v){this.attrs[k]=v}removeAttribute(k){delete this.attrs[k]}hasAttribute(k){return k in this.attrs}
 focus(){this.focused=true}closest(){return this.detail}
}
const controls=[new Control("caseStatus","select","Review"),new Control("assignedTo","select","4"),new Control("noteText","textarea"),new Control("analysisProblem","textarea","Offline"),new Control("oNotes","textarea"),new Control("oResolved","checkbox"),new Control("fileInput","file")];
const hiddenPassword=new Control("credential","password");Object.defineProperty(hiddenPassword,"value",{get(){throw new Error("Credentials must not be inspected by draft tracking")}});
const scope={classList:classes(),querySelectorAll:()=>[...controls,hiddenPassword]},modal={classList:classes(),querySelectorAll:()=>[]};modal.classList.items.add("hidden");
const indicator={classList:classes(),textContent:""},nodes=new Map([...controls.map(c=>[c.id,c]),["caseView",scope],["newModal",modal],["wsDraftState",indicator]]);
const events={},ctx={$:id=>nodes.get(id),document:{addEventListener:(name,fn)=>events[name]=fn},window:{addEventListener:(name,fn)=>events["window:"+name]=fn},setTimeout:()=>{},confirm:()=>false,plannerBrands:()=>{},markConnection:()=>{},me:null};
vm.createContext(ctx);vm.runInContext(fs.readFileSync("refinements-v203.js","utf8"),ctx);
let checks=0;function check(name,run){run();checks++;process.stdout.write("PASS "+name+"\n")}
check("Clean form and password fields excluded",()=>{assert.equal(ctx.wsScopeDirty("caseView"),false);assert.equal(indicator.textContent,"Alles opgeslagen")});
check("Navigation waits for an in-flight save",()=>{ctx.runWorkspaceAction=function(){};ctx.runWorkspaceAction.pending=new Set(["saveCase"]);let warned=false;ctx.toast=()=>warned=true;assert.equal(ctx.wsConfirmLeave(),false);assert.equal(warned,true);ctx.runWorkspaceAction.pending.clear()});
check("Save controls unlock without weakening foreign-context permissions",()=>{ctx.document.querySelectorAll=()=>[nodes.get("caseStatus")];ctx.caseWriteAllowed=()=>true;const release=ctx.wsLockAction("saveCase");assert.equal(nodes.get("caseStatus").disabled,true);ctx.caseWriteAllowed=()=>false;release();assert.equal(nodes.get("caseStatus").disabled,true);nodes.get("caseStatus").disabled=false;ctx.caseWriteAllowed=()=>true});
check("Cancel navigation preserves entered note",()=>{nodes.get("noteText").value="Nog niet verstuurde notitie";assert.equal(ctx.wsConfirmLeave(),false);assert.equal(nodes.get("noteText").value,"Nog niet verstuurde notitie")});
check("Saving only status preserves other editor drafts",()=>{
 nodes.get("caseStatus").value="Ingepland";nodes.get("analysisProblem").value="Nieuwe waarneming";const drafts=ctx.wsCaseDrafts(["status"]);
 nodes.get("noteText").value="";nodes.get("analysisProblem").value="Offline";ctx.wsResetScope("caseView");ctx.wsRestoreCaseDrafts(drafts);
 assert.equal(nodes.get("caseStatus").value,"Ingepland");assert.equal(nodes.get("noteText").value,"Nog niet verstuurde notitie");assert.equal(nodes.get("analysisProblem").value,"Nieuwe waarneming");assert.equal(ctx.wsScopeDirty("caseView"),true)
});
check("Outcome save leaves unrelated note pending",()=>{nodes.get("oNotes").value="Geregistreerde uitkomst";ctx.wsCommitArea("caseView","outcome");assert.equal(ctx.wsScopeDirty("caseView"),true)});
check("File selection remains pending after a partial save",()=>{nodes.get("fileInput").files=[{name:"foto.png",size:12,lastModified:3}];const drafts=ctx.wsCaseDrafts(["status"]);nodes.get("noteText").value="";nodes.get("analysisProblem").value="Offline";ctx.wsResetScope("caseView");ctx.wsRestoreCaseDrafts(drafts);assert.equal(ctx.wsScopeDirty("caseView"),true);assert.equal(nodes.get("fileInput").files.length,1)});
check("Browser unload warns while a draft exists",()=>{let blocked=false;const e={preventDefault:()=>blocked=true};events["window:beforeunload"](e);assert.equal(blocked,true);assert.equal(e.returnValue,"")});
check("Confirmed discard clears pending data and file selection",()=>{ctx.confirm=()=>true;assert.equal(ctx.wsConfirmLeave(),true);assert.equal(ctx.wsScopeDirty("caseView"),false);assert.equal(nodes.get("fileInput").files.length,0);assert.equal(nodes.get("noteText").value,"")});
check("Field errors are visible, focused and connected",()=>{ctx.wsFieldError("analysisProblem","Vul de klacht in.","wsDraftState");assert.equal(nodes.get("analysisProblem").attrs["aria-invalid"],"true");assert.equal(nodes.get("analysisProblem").focused,true);assert.equal(indicator.textContent,"Vul de klacht in.")});
check("Safety advice takes priority over completeness and remote checks",()=>{assert.ok(ctx.wsNextStep({triage_level:"veiligheidsreview",score:100,remote_checks:["test"]}).includes("veiligheid"))});
check("Incomplete and uncertain analyses route to clarification or review",()=>{assert.ok(ctx.wsNextStep({missing:["model"],fault_confidence:"middel"}).includes("ontbrekende"));assert.ok(ctx.wsNextStep({warnings:["tegenstrijdig"],fault_confidence:"hoog"}).includes("technisch beoordelen"))});
process.stdout.write(JSON.stringify({passed:checks,total:checks,mode:"isolated DOM fixture using actual editor helpers"})+"\n");
