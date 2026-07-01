var API_URL = "https://d202tohotwteol.cloudfront.net/salons";
var CONTINENTAL_US_CENTER = { lat: 39.8283, lng: -98.5795 };

var COLORS = {
  violet: "#7d5a8c",
  blue: "#27506b",
  green: "#5b7553",
  yellow: "#c9a227",
  red: "#b7410e"
};

var map = null;
var salonsData = null;

function getColor(value) {
  if (value >= 14.99) return COLORS.red;
  if (value === 12.99 || value === 13.99) return COLORS.yellow;
  if (value >= 9.99 && value <= 12.98) return COLORS.green;
  if (value >= 7.99 && value <= 9.98) return COLORS.blue;
  return COLORS.violet;
}

function getIcon(color, isGeneric) {
  if (isGeneric === "true") {
    // larger square marker for generic / region-wide offers
    return {
      path: "M -10,-10 10,-10 10,10 -10,10 Z",
      fillColor: color,
      fillOpacity: 0.95,
      strokeColor: "#2b2118",
      strokeWeight: 1.5,
      scale: 1
    };
  }
  // circle marker for specific salon addresses
  return {
    path: google.maps.SymbolPath.CIRCLE,
    fillColor: color,
    fillOpacity: 0.95,
    strokeColor: "#2b2118",
    strokeWeight: 1.5,
    scale: 7
  };
}

function showFailureBanner() {
  document.getElementById("failure-banner").classList.add("show");
}

function renderMarkers() {
  if (!map || !salonsData || !Array.isArray(salonsData)) return;

  var infoWindow = new google.maps.InfoWindow();

  salonsData.forEach(function (salon) {
    if (salon.lat == null || salon.long == null) return;

    var color = getColor(salon.value);
    var icon = getIcon(color, salon.is_generic);

    var marker = new google.maps.Marker({
      position: { lat: salon.lat, lng: salon.long },
      map: map,
      icon: icon,
      title: salon.address
    });

    var showInfo = function () {
      var typeLabel = salon.is_generic === "true" ? "Generic / region-wide offer" : "Specific salon";
      var offerUrl = "https://offers.greatclips.com/" + salon.stub;
      infoWindow.setContent(
        '<div class="info-window"><strong>' + salon.address + '</strong><br>' +
        '<a href="' + offerUrl + '" target="_blank" rel="noopener">$' + salon.value.toFixed(2) + '</a>' +
        ' &mdash; ' + typeLabel + '</div>'
      );
      infoWindow.open(map, marker);
    };
    marker.addListener("mouseover", showInfo);
    marker.addListener("click", showInfo);
  });
}

function initMap() {
  map = new google.maps.Map(document.getElementById("map"), {
    center: CONTINENTAL_US_CENTER,
    zoom: 4,
    mapTypeControl: false
  });
  renderMarkers();
}
window.initMap = initMap;

function loadGoogleMapsScript() {
  var script = document.createElement("script");
  script.async = true;
  script.src = "https://maps.googleapis.com/maps/api/js?key=" + window.GOOGLE_MAPS_API_KEY + "&callback=initMap&loading=async";
  document.head.appendChild(script);
}
loadGoogleMapsScript();

// Kicked off immediately, independent of (and in parallel with) the Maps script load.
fetch(API_URL)
  .then(function (res) {
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  })
  .then(function (data) {
    salonsData = data;
    renderMarkers();
  })
  .catch(function (err) {
    console.error("Failed to fetch salons:", err);
    showFailureBanner();
  });

document.getElementById("banner-close").addEventListener("click", function () {
  document.getElementById("failure-banner").classList.remove("show");
});

document.getElementById("report-form").addEventListener("submit", function (e) {
  e.preventDefault();
  document.getElementById("report-confirmation").textContent = "Thanks for the report — we'll take a look.";
  document.getElementById("report-message").value = "";
  document.getElementById("report-char-count").textContent = "0";
});

document.getElementById("report-message").addEventListener("input", function () {
  document.getElementById("report-char-count").textContent = this.value.length;
});
