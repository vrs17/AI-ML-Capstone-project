/* Window-wide drag and drop.
   ---------------------------------------------------------------------------
   Both pages already accepted a drop on a small dashed box. The problem was
   everywhere else: a miss meant the browser navigated to the file itself, which
   silently destroyed a running video session. This captures the whole document,
   so a drop anywhere works and a bad drop can never navigate away.

   Three details that are easy to get wrong and are handled here:
     * dragenter/dragleave fire for every child element, so the overlay flickers
       unless enter/leave are counted rather than treated as on/off;
     * `drop` never fires unless `dragover` calls preventDefault();
     * a drag carrying text (a selection, a link) is not a file drag — checking
       dataTransfer.types for "Files" keeps the overlay from appearing for those.

   The overlay is pointer-events:none, so it is purely visual and the underlying
   page still receives the drop.                                              */

function installDrop({ accept, extensions, onFile, title, hint }) {
  const dz = document.createElement("div");
  dz.className = "dz";
  dz.setAttribute("aria-hidden", "true");
  dz.innerHTML = `<div class="dz-in">
      <svg class="dz-ico" viewBox="0 0 48 48" fill="none" stroke="currentColor"
           stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
        <path d="M24 31V9m0 0-8 8m8-8 8 8"/>
        <path d="M9 31v5a4 4 0 0 0 4 4h22a4 4 0 0 0 4-4v-5"/></svg>
      <div class="dz-t"></div><div class="dz-s"></div></div>`;
  document.body.appendChild(dz);
  const tEl = dz.querySelector(".dz-t"), sEl = dz.querySelector(".dz-s");

  let depth = 0, resetTimer = null;
  const isFileDrag = e => Array.from(e.dataTransfer?.types || []).includes("Files");
  const show = (ok, t, s) => {
    clearTimeout(resetTimer);
    dz.classList.toggle("bad", !ok);
    tEl.textContent = t; sEl.textContent = s;
    dz.classList.add("on");
  };
  const hide = () => { depth = 0; clearTimeout(resetTimer); dz.classList.remove("on", "bad"); };

  addEventListener("dragenter", e => {
    if (!isFileDrag(e)) return;
    e.preventDefault(); depth++;
    show(true, title, hint);
  });
  addEventListener("dragover", e => { if (isFileDrag(e)) e.preventDefault(); });
  addEventListener("dragleave", e => { if (isFileDrag(e) && --depth <= 0) hide(); });
  addEventListener("drop", e => {
    if (!isFileDrag(e)) return;
    e.preventDefault();                       // without this the browser opens the file
    const files = Array.from(e.dataTransfer.files || []);
    const good = files.filter(f => accept.test(f.type || "") ||
                                   extensions.test(f.name || ""));
    if (!good.length) {
      const f = files[0];
      show(false, "That file type is not supported",
           f ? `${f.name} — ${f.type || "unknown type"}` : hint);
      resetTimer = setTimeout(hide, 2200);
      return;
    }
    hide();
    onFile(good[0], good.length);
  });
  addEventListener("dragend", hide);
  addEventListener("blur", hide);
  addEventListener("keydown", e => e.key === "Escape" && hide());
  return { hide };
}
