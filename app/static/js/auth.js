document.addEventListener("DOMContentLoaded", function () {

    const registerForm = document.getElementById("register-form");
    const loginForm = document.getElementById("login-form");

    const serverMessage = document.getElementById("server-message");


    function showMessage(message, type) {

        if (!serverMessage) {
            return;
        }

        serverMessage.textContent = message;

        serverMessage.classList.remove(
            "error",
            "success"
        );

        if (message) {

            serverMessage.style.display = "block";

            serverMessage.classList.add(type);

        } else {

            serverMessage.style.display = "none";

        }
    }


    function showField(id, message, valid) {

        const element = document.getElementById(id);

        if (!element) {
            return;
        }

        element.textContent = message;

        element.classList.remove(
            "field-error",
            "field-success"
        );

        if (message) {

            if (valid) {
                element.classList.add("field-success");
            } else {
                element.classList.add("field-error");
            }

        }

    }


    function setInput(input, valid) {

        if (!input) {
            return;
        }

        input.classList.remove(
            "input-error",
            "input-success"
        );

        if (input.value.trim() !== "") {

            if (valid) {
                input.classList.add("input-success");
            } else {
                input.classList.add("input-error");
            }

        }

    }


    function validEmail(value) {

        return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);

    }


    function passwordScore(value) {

        let score = 0;

        if (value.length >= 6) {
            score++;
        }

        if (value.length >= 10) {
            score++;
        }

        if (/[A-Z]/.test(value)) {
            score++;
        }

        if (/[a-z]/.test(value)) {
            score++;
        }

        if (/[0-9]/.test(value)) {
            score++;
        }

        if (/[^A-Za-z0-9]/.test(value)) {
            score++;
        }

        return score;

    }


    if (registerForm) {

        const name = document.getElementById("name");
        const email = document.getElementById("email");
        const password = document.getElementById("password");
        const confirmPassword =
            document.getElementById("confirm_password");

        const strengthFill =
            document.getElementById("strength-fill");

        const strengthText =
            document.getElementById("strength-text");


        function checkName() {

            const value = name.value.trim();

            if (value === "") {

                showField(
                    "name-message",
                    "Name is required.",
                    false
                );

                setInput(name, false);

                return false;

            }

            if (value.length < 2) {

                showField(
                    "name-message",
                    "Name must contain at least 2 characters.",
                    false
                );

                setInput(name, false);

                return false;

            }

            showField(
                "name-message",
                "Name is valid.",
                true
            );

            setInput(name, true);

            return true;

        }


        function checkEmail() {

            const value = email.value.trim();

            if (value === "") {

                showField(
                    "email-message",
                    "Email is required.",
                    false
                );

                setInput(email, false);

                return false;

            }

            if (!validEmail(value)) {

                showField(
                    "email-message",
                    "Enter a valid email address.",
                    false
                );

                setInput(email, false);

                return false;

            }

            showField(
                "email-message",
                "Email is valid.",
                true
            );

            setInput(email, true);

            return true;

        }


        function checkPassword() {

            const value = password.value;

            if (value === "") {

                showField(
                    "password-message",
                    "Password is required.",
                    false
                );

                setInput(password, false);

                strengthText.textContent =
                    "Enter a password";

                strengthFill.style.width = "0%";

                return false;

            }

            if (value.length < 6) {

                showField(
                    "password-message",
                    "Password must contain at least 6 characters.",
                    false
                );

                setInput(password, false);

                strengthText.textContent =
                    "Too short";

                strengthFill.style.width = "25%";

                return false;

            }


            const score = passwordScore(value);


            if (score <= 2) {

                strengthText.textContent =
                    "Weak";

                strengthFill.style.width = "35%";

            } else if (score <= 4) {

                strengthText.textContent =
                    "Moderate";

                strengthFill.style.width = "65%";

            } else {

                strengthText.textContent =
                    "Strong";

                strengthFill.style.width = "100%";

            }


            showField(
                "password-message",
                "Password is valid.",
                true
            );

            setInput(password, true);

            return true;

        }


        function checkConfirmPassword() {

            const value = confirmPassword.value;

            if (value === "") {

                showField(
                    "confirm-message",
                    "Please confirm your password.",
                    false
                );

                setInput(confirmPassword, false);

                return false;

            }

            if (value !== password.value) {

                showField(
                    "confirm-message",
                    "Passwords do not match.",
                    false
                );

                setInput(confirmPassword, false);

                return false;

            }

            showField(
                "confirm-message",
                "Passwords match.",
                true
            );

            setInput(confirmPassword, true);

            return true;

        }


        name.addEventListener(
            "input",
            checkName
        );

        email.addEventListener(
            "input",
            checkEmail
        );

        password.addEventListener(
            "input",
            function () {

                checkPassword();

                if (confirmPassword.value !== "") {
                    checkConfirmPassword();
                }

            }
        );

        confirmPassword.addEventListener(
            "input",
            checkConfirmPassword
        );


        registerForm.addEventListener(
            "submit",
            function (event) {

                const nameValid = checkName();
                const emailValid = checkEmail();
                const passwordValid = checkPassword();
                const confirmValid =
                    checkConfirmPassword();


                if (
                    !nameValid ||
                    !emailValid ||
                    !passwordValid ||
                    !confirmValid
                ) {

                    event.preventDefault();

                    showMessage(
                        "Please correct the highlighted fields.",
                        "error"
                    );

                    return;

                }


                const button =
                    document.getElementById(
                        "register-button"
                    );

                const buttonText =
                    document.getElementById(
                        "register-button-text"
                    );

                button.disabled = true;

                buttonText.textContent =
                    "CREATING ACCOUNT...";

            }
        );

    }


    if (loginForm) {

        const email =
            document.getElementById("email");

        const password =
            document.getElementById("password");


        function checkLoginEmail() {

            const value = email.value.trim();

            if (value === "") {

                showField(
                    "email-message",
                    "Email is required.",
                    false
                );

                setInput(email, false);

                return false;

            }

            if (!validEmail(value)) {

                showField(
                    "email-message",
                    "Enter a valid email address.",
                    false
                );

                setInput(email, false);

                return false;

            }

            showField(
                "email-message",
                "Email is valid.",
                true
            );

            setInput(email, true);

            return true;

        }


        function checkLoginPassword() {

            const value = password.value;

            if (value === "") {

                showField(
                    "password-message",
                    "Password is required.",
                    false
                );

                setInput(password, false);

                return false;

            }

            showField(
                "password-message",
                "",
                true
            );

            setInput(password, true);

            return true;

        }


        email.addEventListener(
            "input",
            checkLoginEmail
        );

        password.addEventListener(
            "input",
            checkLoginPassword
        );


        loginForm.addEventListener(
            "submit",
            function (event) {

                const emailValid =
                    checkLoginEmail();

                const passwordValid =
                    checkLoginPassword();


                if (!emailValid || !passwordValid) {

                    event.preventDefault();

                    showMessage(
                        "Please enter valid login details.",
                        "error"
                    );

                    return;

                }


                const button =
                    document.getElementById(
                        "login-button"
                    );

                const buttonText =
                    document.getElementById(
                        "login-button-text"
                    );

                button.disabled = true;

                buttonText.textContent =
                    "SIGNING IN...";

            }
        );

    }

});