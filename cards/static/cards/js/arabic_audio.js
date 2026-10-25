(() => {
    const setState = (button, state, label) => {
        button.dataset.state = state;
        button.querySelector(".arabic-audio-label").textContent = label;
        button.querySelector(".arabic-audio-icon").textContent =
            state === "playing" ? "❚❚" : "▶";
        button.setAttribute(
            "aria-label",
            state === "playing"
                ? "Pause Arabic pronunciation"
                : state === "loading"
                  ? "Loading Arabic pronunciation"
                  : state === "error"
                    ? "Arabic audio unavailable. Try again"
                    : "Play pronunciation of the Arabic answer",
        );
    };

    document.querySelectorAll(".arabic-audio").forEach((container) => {
        const audio = container.querySelector(".arabic-audio-player");
        const button = container.querySelector("[data-audio-button]");
        if (!audio || !button) {
            return;
        }

        audio.controls = false;
        audio.classList.add("is-enhanced");
        button.hidden = false;

        button.addEventListener("click", async () => {
            if (button.dataset.state === "loading") {
                return;
            }
            if (!audio.paused) {
                audio.pause();
                return;
            }

            document.querySelectorAll(".arabic-audio-player").forEach((other) => {
                if (other !== audio) {
                    other.pause();
                }
            });

            setState(button, "loading", "Loading…");
            try {
                await audio.play();
            } catch (error) {
                setState(button, "error", "Try again");
            }
        });

        audio.addEventListener("play", () => {
            setState(button, "playing", "Pause");
        });
        audio.addEventListener("pause", () => {
            setState(button, "idle", "Listen");
        });
        audio.addEventListener("ended", () => {
            setState(button, "idle", "Listen");
        });
        audio.addEventListener("error", () => {
            setState(button, "error", "Unavailable");
        });
    });
})();
