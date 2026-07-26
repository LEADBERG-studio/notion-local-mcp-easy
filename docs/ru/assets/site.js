document.addEventListener("DOMContentLoaded", () => {
  const btn = document.querySelector(".nav-toggle");
  if (btn) btn.addEventListener("click", () => document.body.classList.toggle("nav-open"));
  for (const a of document.querySelectorAll(".sidebar a")) {
    a.addEventListener("click", () => document.body.classList.remove("nav-open"));
  }
});
