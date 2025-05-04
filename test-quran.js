const axios = require('axios');
const fs = require('fs');

// Helper function to delay (polite API usage)
function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function fetchQuran() {
    try {
        const response = await axios.get('https://api.quran.gading.dev/surah');
        const surahs = response.data.data;

        console.log('Successfully fetched list of Surahs.');

        // Loop through Surahs and fetch the verses
        for (const surah of surahs) {
            console.log(`\nFetching Surah ${surah.number}: ${surah.name.short} (${surah.name.transliteration.en})...`);

            // Fetch Surah details and verses
            const surahResponse = await axios.get(`https://api.quran.gading.dev/surah/${surah.number}`);
            const verses = surahResponse.data.data.verses;

            // Save the Surah name and number of verses
            console.log(`✅ Saved Surah ${surah.number}: ${surah.name.transliteration.en} with ${surah.numberOfVerses} verses.`);

            // Save verses to a file or display them
            const surahData = {
                surahName: surah.name.transliteration.en,
                verses: verses.map(verse => ({
                    number: verse.number.inSurah,
                    arabicText: verse.text.arab,
                    translation: verse.translation.en
                }))
            };

            // Saving to a JSON file (optional)
            const filePath = `surah_${surah.number}.json`;
            fs.writeFileSync(filePath, JSON.stringify(surahData, null, 2));
            console.log(`Surah ${surah.number} saved as ${filePath}.\n`);

            // Display verses in the console (you can customize this)
            verses.forEach(verse => {
                console.log(
                    `Ayah ${verse.number.inSurah}: ${verse.text.arab} | Translation: ${verse.translation.en}`
                );
            });

            // Delay between requests to avoid hammering the API
            await sleep(1000); // 1 second pause
        }

    } catch (error) {
        console.error('Error fetching Quran data:', error.message);
    }
}

fetchQuran();
