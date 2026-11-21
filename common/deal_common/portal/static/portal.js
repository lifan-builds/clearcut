"use strict";
(() => {
  const reel = document.getElementById("worlds-reel");
  if (!reel) return;
  const cards = [...reel.querySelectorAll(".world-monolith")];
  const prevBtn = document.getElementById("reel-prev");
  const nextBtn = document.getElementById("reel-next");
  const counterEl = document.getElementById("reel-counter");
  const progressBar = document.getElementById("reel-progress-bar");
  const dockPills = [...document.querySelectorAll(".dock-pill[data-world-target]")];
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let activeIdx = 0;

  function setActive(index) {
    activeIdx = Math.max(0, Math.min(cards.length - 1, index));
    const activeCard = cards[activeIdx];
    const world = activeCard?.dataset.world || "snow";
    document.body.dataset.activeWorld = world;
    cards.forEach((c, i) => c.classList.toggle("is-active", i === activeIdx));
    dockPills.forEach((p) => p.classList.toggle("is-current", p.dataset.worldTarget === world));
    if (counterEl) {
      counterEl.textContent = `0${activeIdx + 1} / 0${cards.length}`;
    }
    if (progressBar) {
      progressBar.style.width = `${((activeIdx + 1) / cards.length) * 100}%`;
    }
  }

  function scrollToCard(index) {
    const target = cards[Math.max(0, Math.min(cards.length - 1, index))];
    if (!target) return;
    const padLeft = parseFloat(getComputedStyle(reel).paddingLeft) || 0;
    reel.scrollTo({
      left: target.offsetLeft - padLeft,
      behavior: reducedMotion ? "auto" : "smooth",
    });
  }

  function syncFromScroll() {
    const viewportCenter = reel.scrollLeft + reel.clientWidth * 0.38;
    let closest = 0;
    let minDist = Infinity;
    cards.forEach((card, idx) => {
      const center = card.offsetLeft + card.offsetWidth * 0.5;
      const dist = Math.abs(center - viewportCenter);
      if (dist < minDist) {
        minDist = dist;
        closest = idx;
      }
      if (!reducedMotion) {
        const offsetRatio = Math.max(-1, Math.min(1, (card.offsetLeft - reel.scrollLeft) / Math.max(1, reel.clientWidth)));
        card.style.setProperty("--parallax-x", `${Math.round(offsetRatio * -16)}px`);
      }
    });
    setActive(closest);
  }

  let rafPending = false;
  reel.addEventListener(
    "scroll",
    () => {
      if (rafPending) return;
      rafPending = true;
      requestAnimationFrame(() => {
        rafPending = false;
        syncFromScroll();
      });
    },
    { passive: true }
  );

  reel.addEventListener(
    "wheel",
    (event) => {
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
      const maxScroll = reel.scrollWidth - reel.clientWidth;
      if (maxScroll <= 4) return;
      const atStart = reel.scrollLeft <= 2 && event.deltaY < 0;
      const atEnd = reel.scrollLeft >= maxScroll - 2 && event.deltaY > 0;
      if (atStart || atEnd) return;
      event.preventDefault();
      reel.scrollLeft += event.deltaY;
    },
    { passive: false }
  );

  let dragStartX = 0;
  let dragScrollLeft = 0;
  let dragging = false;
  reel.addEventListener("pointerdown", (event) => {
    if (event.pointerType !== "mouse" || event.button !== 0 || event.target.closest("a, button")) return;
    dragging = true;
    dragStartX = event.clientX;
    dragScrollLeft = reel.scrollLeft;
    reel.classList.add("is-dragging");
  });
  window.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    reel.scrollLeft = dragScrollLeft - (event.clientX - dragStartX);
  });
  window.addEventListener("pointerup", () => {
    if (!dragging) return;
    dragging = false;
    reel.classList.remove("is-dragging");
  });

  prevBtn?.addEventListener("click", () => scrollToCard(activeIdx - 1));
  nextBtn?.addEventListener("click", () => scrollToCard(activeIdx + 1));

  reel.addEventListener("keydown", (event) => {
    if (event.key === "ArrowRight") {
      event.preventDefault();
      scrollToCard(activeIdx + 1);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      scrollToCard(activeIdx - 1);
    }
  });

  cards.forEach((card, idx) => {
    card.addEventListener("mouseenter", () => setActive(idx));
    card.addEventListener("focusin", () => setActive(idx));
  });

  syncFromScroll();
})();
