document.querySelectorAll(".toggle-password").forEach(function (button) {
    const input = document.getElementById(button.getAttribute("aria-controls"));

    if (!input) {
        return;
    }

    button.addEventListener("click", function () {
        const reveal = input.type === "password";

        input.type = reveal ? "text" : "password";
        button.setAttribute("aria-pressed", String(reveal));
        button.setAttribute("aria-label", reveal ? "Hide password" : "Show password");
    });
});
