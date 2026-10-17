/* /rates: read the two rate cards, preview the split, save or reset them.
 *
 * The page ships the state as window.RATES (see rates.html); saving returns the
 * same shape, so applyState() re-renders both cards from one code path.
 */
(function () {
  "use strict";

  var state = window.RATES || {};
  var summonIds = ["fourStarPercent", "threeStarPercent", "fourStarCostumeShare",
    "threeStarCostumeShare", "featuredPercent"];

  function $(id) { return document.getElementById(id); }

  function num(id) {
    var got = parseFloat($(id).value);
    return isFinite(got) ? got : 0;
  }

  function show(kind, message) {
    var box = $(kind === "error" ? "rates-error" : "rates-ok");
    box.textContent = message;
    box.hidden = false;
    clearTimeout(box._timer);
    box._timer = setTimeout(function () { box.hidden = true; }, 6000);
  }

  function hideBanners() {
    $("rates-error").hidden = true;
    $("rates-ok").hidden = true;
  }

  function round3(value) {
    var got = Math.round(parseFloat(value) * 1000) / 1000;
    return isFinite(got) ? got : 0;
  }

  function setChip(id, source) {
    var chip = $(id);
    chip.className = "chip";
    if (source === "file") { chip.className = "chip on"; chip.textContent = "SAVED"; }
    else if (source === "invalid") { chip.className = "chip warn"; chip.textContent = "INVALID FILE"; }
    else { chip.textContent = "DEFAULT"; }
  }

  function preview() {
    var four = num("fourStarPercent"), three = num("threeStarPercent");
    var fs = num("fourStarCostumeShare") / 100, ts = num("threeStarCostumeShare") / 100;
    var fmt = function (v) { return (Math.round(v * 100) / 100).toString(); };
    $("p-four-c").textContent = fmt(four * fs);
    $("p-four-w").textContent = fmt(four * (1 - fs));
    $("p-three-c").textContent = fmt(three * ts);
    $("p-three-w").textContent = fmt(three * (1 - ts));
    $("p-two-w").textContent = fmt(100 - four - three);

    var sum = 0, weighted = 0, gems = state.roulette.gems || [];
    document.querySelectorAll(".roulette-pct").forEach(function (input) {
      var value = parseFloat(input.value);
      if (!isFinite(value)) { value = 0; }
      input.classList.toggle("bad", value < 0 || value > 100);
      sum += value;
      weighted += value * (gems[parseInt(input.dataset.index, 10)] || 0);
    });
    var sumFixed = Math.round(sum * 1000) / 1000;
    $("roulette-total").textContent = sumFixed.toString();
    $("roulette-total").classList.toggle("bad", Math.abs(sum - 100) > 0.001);
    var ok = Math.abs(sum - 100) <= 0.001 && sum > 0;
    $("roulette-avg-wrap").hidden = !ok;
    if (ok) { $("roulette-avg").textContent = (Math.round(weighted / sum * 10) / 10).toString(); }
  }

  function applyState(fresh) {
    if (!fresh) { return; }
    state = fresh;
    var summon = fresh.summon.rates;
    summonIds.forEach(function (id) { $(id).value = round3(summon[id]); });
    $("multiMinRarity").value = String(summon.multiMinRarity);
    $("stepUpBoost").checked = !!summon.stepUpBoost;
    setChip("summon-source", fresh.summon.source);

    (fresh.roulette.percents || []).forEach(function (value, index) {
      var input = document.querySelector('.roulette-pct[data-index="' + index + '"]');
      if (input) { input.value = round3(value); }
    });
    setChip("roulette-source", fresh.roulette.source);
    preview();
  }

  function post(url, body) {
    hideBanners();
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok || !data.ok) { throw new Error(data.error || "Request failed"); }
        return data;
      });
    });
  }

  function summonPayload() {
    return {
      kind: "summon",
      rates: {
        fourStarPercent: num("fourStarPercent"),
        threeStarPercent: num("threeStarPercent"),
        fourStarCostumeShare: num("fourStarCostumeShare"),
        threeStarCostumeShare: num("threeStarCostumeShare"),
        featuredPercent: num("featuredPercent"),
        multiMinRarity: parseInt($("multiMinRarity").value, 10),
        stepUpBoost: $("stepUpBoost").checked
      }
    };
  }

  function roulettePayload() {
    var percents = [];
    document.querySelectorAll(".roulette-pct").forEach(function (input) {
      var value = parseFloat(input.value);
      percents.push(isFinite(value) ? value : 0);
    });
    return { kind: "roulette", percents: percents };
  }

  function presets(name) {
    var d = state.default_summon || {};
    if (name === "summon-default") {
      summonIds.forEach(function (id) { $(id).value = d[id]; });
      $("multiMinRarity").value = String(d.multiMinRarity);
      $("stepUpBoost").checked = !!d.stepUpBoost;
    } else if (name === "summon-generous") {
      $("fourStarPercent").value = 10;
      $("threeStarPercent").value = 25;
      $("fourStarCostumeShare").value = 50;
      $("threeStarCostumeShare").value = 50;
      $("featuredPercent").value = 50;
      $("multiMinRarity").value = "4";
      $("stepUpBoost").checked = true;
    } else if (name === "summon-featured") {
      $("fourStarPercent").value = 5;
      $("threeStarPercent").value = 15;
      $("fourStarCostumeShare").value = 40;
      $("threeStarCostumeShare").value = 33.333;
      $("featuredPercent").value = 80;
      $("multiMinRarity").value = "3";
      $("stepUpBoost").checked = true;
    } else if (name === "roulette-default") {
      (state.default_roulette || []).forEach(function (value, index) {
        document.querySelector('.roulette-pct[data-index="' + index + '"]').value = value;
      });
    } else if (name === "roulette-lucky") {
      [30, 25, 20, 15, 10].forEach(function (value, index) {
        document.querySelector('.roulette-pct[data-index="' + index + '"]').value = value;
      });
    } else if (name === "roulette-even") {
      document.querySelectorAll(".roulette-pct").forEach(function (input) { input.value = 20; });
    }
    preview();
  }

  document.addEventListener("click", function (event) {
    var target = event.target.closest("[data-preset]");
    if (target) { presets(target.dataset.preset); }
  });

  document.querySelectorAll(".rnum").forEach(function (input) {
    input.addEventListener("input", preview);
    input.addEventListener("change", preview);
  });

  $("summon-save").addEventListener("click", function () {
    post("/rates/save", summonPayload())
      .then(function (data) { applyState(data); show("ok", "Summon rates saved."); })
      .catch(function (error) { show("error", error.message); });
  });

  $("roulette-save").addEventListener("click", function () {
    if (Math.abs(parseFloat($("roulette-total").textContent) - 100) > 0.001) {
      show("error", "The roulette chances must add up to 100%.");
      return;
    }
    post("/rates/save", roulettePayload())
      .then(function (data) { applyState(data); show("ok", "Roulette chances saved."); })
      .catch(function (error) { show("error", error.message); });
  });

  $("summon-reset").addEventListener("click", function () {
    post("/rates/reset", { kind: "summon" })
      .then(function (data) { applyState(data); show("ok", "Summon rates back to the defaults."); })
      .catch(function (error) { show("error", error.message); });
  });

  $("roulette-reset").addEventListener("click", function () {
    post("/rates/reset", { kind: "roulette" })
      .then(function (data) { applyState(data); show("ok", "Roulette chances back to the defaults."); })
      .catch(function (error) { show("error", error.message); });
  });

  applyState(state);
})();
