(() => {
    const form = document.getElementById("idle-logout-form");
    if (!form) return;

    const timeoutMs = (Number(form.dataset.idleTimeout) || 300) * 1000;
    const heartbeatUrl = form.dataset.heartbeatUrl;
    const loginUrl = form.dataset.loginUrl;
    const csrfInput = form.querySelector("input[name='csrfmiddlewaretoken']");
    const csrfToken = csrfInput ? csrfInput.value : "";
    const activityKey = "flashcards.lastActivity";
    const heartbeatIntervalMs = 60 * 1000;

    let lastActivity = Date.now();
    let lastHeartbeat = 0;
    let lastActivityWrite = 0;
    let heartbeatInFlight = false;
    let loggingOut = false;

    const readSharedActivity = () => {
        try {
            return Number(localStorage.getItem(activityKey)) || 0;
        } catch (error) {
            return 0;
        }
    };

    const writeSharedActivity = (now, force = false) => {
        if (!force && now - lastActivityWrite < 1000) return;
        lastActivityWrite = now;
        try {
            localStorage.setItem(activityKey, String(now));
        } catch (error) {
            // Storage can be unavailable in private browsing.
        }
    };

    const sendHeartbeat = () => {
        if (
            !heartbeatUrl
            || !csrfToken
            || heartbeatInFlight
            || loggingOut
        ) {
            return;
        }

        heartbeatInFlight = true;
        lastHeartbeat = Date.now();

        fetch(heartbeatUrl, {
            method: "POST",
            headers: { "X-CSRFToken": csrfToken },
            credentials: "same-origin",
        })
            .then((response) => {
                if (response.status === 401 && loginUrl) {
                    window.location.assign(loginUrl);
                    return;
                }
                if (response.redirected) {
                    window.location.assign(response.url);
                }
            })
            .catch(() => {
                // A failed heartbeat is retried on the next activity.
            })
            .finally(() => {
                heartbeatInFlight = false;
            });
    };

    const isIdle = () => {
        const now = Date.now();
        const shared = readSharedActivity();
        if (shared > lastActivity) {
            lastActivity = shared;
        }
        return now - lastActivity >= timeoutMs;
    };

    const markActivity = () => {
        if (loggingOut) return;
        const now = Date.now();
        lastActivity = now;
        writeSharedActivity(now);
        if (now - lastHeartbeat >= heartbeatIntervalMs) {
            sendHeartbeat();
        }
    };

    const logout = () => {
        if (loggingOut) return;
        if (!isIdle()) {
            lastActivity = Date.now();
            return;
        }
        loggingOut = true;
        form.submit();
    };

    const checkIdle = () => {
        if (isIdle()) logout();
    };

    const handleVisibility = () => {
        if (document.hidden) return;
        if (isIdle()) {
            logout();
        } else {
            markActivity();
        }
    };

    [
        "pointerdown",
        "keydown",
        "wheel",
        "touchstart",
        "scroll",
    ].forEach((eventName) => {
        document.addEventListener(eventName, markActivity, {
            passive: true,
            capture: true,
        });
    });

    document.addEventListener("visibilitychange", handleVisibility);
    window.addEventListener("focus", handleVisibility);
    window.addEventListener("pageshow", handleVisibility);
    window.addEventListener("storage", (event) => {
        if (event.key !== activityKey) return;
        const shared = Number(event.newValue) || 0;
        if (shared > lastActivity) {
            lastActivity = shared;
        }
    });

    lastActivity = Math.max(lastActivity, readSharedActivity());
    writeSharedActivity(lastActivity, true);
    sendHeartbeat();
    window.setInterval(checkIdle, 1000);
})();
