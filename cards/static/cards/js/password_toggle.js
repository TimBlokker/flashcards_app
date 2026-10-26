(() => {
    const inputs = document.querySelectorAll(".auth-panel input[type='password']");

    inputs.forEach((input) => {
        if (!input.parentNode || input.closest(".password-field")) return;

        const wrapper = document.createElement("span");
        wrapper.className = "password-field";
        input.parentNode.insertBefore(wrapper, input);
        wrapper.appendChild(input);

        const label = input.labels?.[0]?.textContent?.trim() || "password";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "password-toggle";
        button.textContent = "Show";
        button.setAttribute("aria-label", `Show ${label.toLowerCase()}`);
        button.setAttribute("aria-pressed", "false");
        wrapper.appendChild(button);

        button.addEventListener("click", () => {
            const showing = input.type === "text";
            input.type = showing ? "password" : "text";
            button.textContent = showing ? "Show" : "Hide";
            button.setAttribute(
                "aria-label",
                `${showing ? "Show" : "Hide"} ${label.toLowerCase()}`,
            );
            button.setAttribute("aria-pressed", showing ? "false" : "true");
        });
    });
})();
