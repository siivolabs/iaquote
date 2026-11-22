document.addEventListener("DOMContentLoaded",()=>{
  const add=document.querySelector("[data-add-line]");
  if(add){add.addEventListener("click",()=>{const host=document.querySelector("#quote-lines");const line=host.querySelector(".quote-line").cloneNode(true);line.querySelector("select").value="";line.querySelector("input").value=1;host.appendChild(line)})}
  const card=document.querySelector("[data-job-id]");
  if(card){const id=card.dataset.jobId;const poll=async()=>{try{const r=await fetch(`/api/jobs/${id}`,{cache:"no-store"});if(!r.ok)return;const j=await r.json();document.querySelector("#progress-bar").style.width=`${j.progress}%`;document.querySelector("#progress-label").textContent=`${j.processed} / ${j.total} · ${j.progress.toFixed(1)} %`;document.querySelector("#job-status").textContent=j.status;document.querySelector("#ok-count").textContent=`${j.succeeded} vérifiés`;document.querySelector("#fail-count").textContent=`${j.failed} échecs`;if(["queued","running"].includes(j.status))setTimeout(poll,1200);else setTimeout(()=>location.reload(),700)}catch(_){setTimeout(poll,2500)}};poll()}
});
