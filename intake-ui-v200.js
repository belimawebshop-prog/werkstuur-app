"use strict";
function linkIntakeLabels(){document.querySelectorAll(".field").forEach(field=>{const label=field.querySelector("label"),control=field.querySelector("input,textarea,select");if(label&&control?.id)label.htmlFor=control.id});document.querySelectorAll(".progress-step").forEach(step=>{step.setAttribute("aria-current",step.classList.contains("active")?"step":"false")})}
let labelsQueued=false;new MutationObserver(()=>{if(!labelsQueued){labelsQueued=true;queueMicrotask(()=>{labelsQueued=false;linkIntakeLabels()})}}).observe(document.body,{childList:true,subtree:true});linkIntakeLabels();
document.querySelectorAll(".err").forEach(el=>el.setAttribute("aria-live","polite"));
let intakeTouched=false;
document.addEventListener("input",event=>{if(byId("form")?.contains(event.target)){intakeTouched=true;event.target.removeAttribute("aria-invalid")}});
document.addEventListener("change",event=>{if(byId("form")?.contains(event.target))intakeTouched=true});
window.addEventListener("beforeunload",event=>{if(intakeTouched&&!byId("form")?.classList.contains("hidden")){event.preventDefault();event.returnValue=""}});
validateStep=((original)=>function(step){
 const valid=original(step),messageId=step===1?"step1Error":"step2Error",fields=step===1?["customer","city"]:["type","manufacturer","problem"];
 fields.forEach(id=>{const control=byId(id),invalid=!control.value.trim();control.setAttribute("aria-invalid",String(invalid));control.setAttribute("aria-describedby",messageId)});
 if(step===1){
  const email=byId("email"),phone=byId("phone"),noContact=!email.value.trim()&&!phone.value.trim();
  for(const control of [email,phone]){control.setAttribute("aria-invalid",String(noContact));control.setAttribute("aria-describedby","contactHint "+messageId)}
  if(!valid){byId(fields.find(id=>!byId(id).value.trim()))?.focus();if(noContact&&fields.every(id=>byId(id).value.trim()))phone.focus();return false}
  if(email.value.trim()&&(!email.checkValidity()||!/^[^@\s<>"(),;:\[\]\\]+@[^@\s]+\.[^@\s]+$/.test(email.value.trim()))){showError(messageId,"Vul een geldig e-mailadres in.");email.setAttribute("aria-invalid","true");email.focus();return false}
  const number=phone.value.trim();
  if(number&&(!/^\+?[0-9 ()./\-]+$/.test(number)||number.replace(/\D/g,"").length<7||number.replace(/\D/g,"").length>15)){showError(messageId,"Vul een geldig telefoonnummer in (7 tot 15 cijfers, eventueel met landcode).");phone.setAttribute("aria-invalid","true");phone.focus();return false}
 }
 if(!valid)byId(fields.find(id=>!byId(id).value.trim()))?.focus();return valid;
})(validateStep);
setStep=((original)=>function(step){original(step);const heading=document.querySelector(`.step[data-step="${currentStep}"] h2`);if(heading){heading.tabIndex=-1;heading.focus({preventScroll:true})}})(setStep);
