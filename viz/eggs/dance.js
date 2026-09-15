// Konami code dance party easter egg
(function () {
  var konami = [
    "ArrowUp", "ArrowUp", "ArrowDown", "ArrowDown",
    "ArrowLeft", "ArrowRight", "ArrowLeft", "ArrowRight",
    "b", "a"
  ];
  var idx = 0;
  var partyUntil = 0;
  var running = false;

  function isInputField(el) {
    return el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT");
  }

  function startParty() {
    if (running) return;
    running = true;
    partyUntil = performance.now() / 1000 + 10;
    if (!matchMedia("(prefers-reduced-motion: reduce)").matches) confetti();
    banner("DANCE PARTY", "ok");
    var partyHandler = function (t) {
      if (t >= partyUntil) {
        var idx = EGG_UPDATE.indexOf(partyHandler);
        if (idx >= 0) EGG_UPDATE.splice(idx, 1);
        running = false;
      } else {
        for (var c of everyone()) c.anim = "dance";
      }
    };
    EGG_UPDATE.push(partyHandler);
  }

  document.addEventListener("keydown", function (e) {
    if (isInputField(document.activeElement)) return;
    if (e.key === konami[idx]) {
      idx++;
      if (idx === konami.length) {
        idx = 0;
        startParty();
      }
    } else {
      idx = 0;
    }
  });

  if (new URLSearchParams(location.search).get("egg") === "dance") {
    startParty();
  }
})();
