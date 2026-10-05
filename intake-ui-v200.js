"use strict";
function linkIntakeLabels(){document.querySelectorAll(".field").forEach(field=>{const label=field.querySelector("label"),control=field.querySelector("input,textarea,select");if(label&&control?.id)label.htmlFor=control.id});document.querySelectorAll(".progress-step").forEach(step=>{step.setAttribute("aria-current",step.classList.contains("active")?"step":"false")})}
let labelsQueued=false;new MutationObserver(()=>{if(!labelsQueued){labelsQueued=true;queueMicrotask(()=>{labelsQueued=false;linkIntakeLabels()})}}).observe(document.body,{childList:true,subtree:true});linkIntakeLabels();
document.querySelectorAll(".err").forEach(el=>el.setAttribute("aria-live","polite"));
