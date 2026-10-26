(() => {
    const form = document.querySelector(".auth-panel form[method='post']");
    const tokenInput = form?.querySelector("input[name='csrfmiddlewaretoken']");
    if (!form || !tokenInput) return;

    const refreshToken = () => {
        const cookie = document.cookie
            .split(";")
            .map((part) => part.trim())
            .find((part) => part.startsWith("csrftoken="));
        if (cookie) tokenInput.value = cookie.slice("csrftoken=".length);
    };

    const markTabActive = () => {
        if (!form.querySelector("input[type='password']")) return;
        try {
            if (!sessionStorage.getItem("flashcards.tab")) {
                sessionStorage.setItem("flashcards.tab", "active");
            }
        } catch (error) {
            // Storage can be unavailable in private browsing.
        }
    };

    window.addEventListener("pageshow", refreshToken);
    form.addEventListener("submit", () => {
        refreshToken();
        markTabActive();
    });
})();
