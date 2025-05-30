// Path to your DATA folder inside static
const DATA_PATH = '/static/DATA/';

const surahSelect = document.getElementById('surah-select');
const ayahList = document.getElementById('ayah-list');

// Load surah.json and populate dropdown
fetch(DATA_PATH + 'surah.json')
  .then(res => res.json())
  .then(surahs => {
    surahs.forEach(surah => {
      const option = document.createElement('option');
      option.value = surah.fileName;  // e.g. "surah_Al-fatihah.json"
      option.textContent = surah.name; // e.g. "Al-Fatihah"
      surahSelect.appendChild(option);
    });
    // Load first Surah by default
    if (surahs.length > 0) {
      loadSurah(surahs[0].fileName);
    }
  })
  .catch(err => {
    ayahList.textContent = 'Failed to load Surah list.';
    console.error(err);
  });

surahSelect.addEventListener('change', (e) => {
  loadSurah(e.target.value);
});

function loadSurah(fileName) {
  ayahList.innerHTML = 'Loading...';
  fetch(DATA_PATH + fileName)
    .then(res => res.json())
    .then(surahData => {
      displayAyahs(surahData.ayahs);
    })
    .catch(err => {
      ayahList.textContent = 'Failed to load Surah data.';
      console.error(err);
    });
}

function displayAyahs(ayahs) {
  ayahList.innerHTML = '';
  ayahs.forEach(ayah => {
    const p = document.createElement('p');
    p.innerHTML = `<strong>Ayah ${ayah.number}</strong>: ${ayah.text}`;
    ayahList.appendChild(p);
  });
}
