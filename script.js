document.addEventListener('DOMContentLoaded', function () {
    const askForm = document.getElementById('askForm');
    const askInput = document.getElementById('askInput');
    const answerBox = document.getElementById('answerBox');

    askForm.addEventListener('submit', function (e) {
        e.preventDefault();
        const question = askInput.value.trim();
        if (!question) return;

        answerBox.textContent = 'Thinking...';

        fetch('/ask', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ question: question }),
        })
            .then((response) => response.json())
            .then((data) => {
                answerBox.textContent = data.answer;
            })
            .catch((error) => {
                console.error('Error:', error);
                answerBox.textContent = 'Error. Please try again later.';
            });
    });
});
