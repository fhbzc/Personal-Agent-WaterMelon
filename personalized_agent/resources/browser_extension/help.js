/* What went wrong, as the browser said it - and the page in the browser's
   language, where the extension has its words. */
(function () {
  const ext = globalThis.browser || globalThis.chrome;
  for (const id of ["head", "do", "then"]) {
    try {
      const said = ext.i18n.getMessage("help_" + id);
      if (said) document.getElementById(id).textContent = said;
    } catch (_err) { /* the English stands */ }
  }
  const why = decodeURIComponent((location.hash || "").slice(1));
  if (why) {
    const box = document.getElementById("why");
    box.textContent = why;
    box.hidden = false;
  }
})();
