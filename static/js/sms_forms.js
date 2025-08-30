// Formating phone numbers using libphonenumber-js
function initPhoneField() {
  const region = (navigator.language || 'en-US').split('-')[1] || 'US';
  const input       = document.querySelector('input#phone_number_input');
  const inputE164   = document.querySelector('input#phone_number_hidden');

  if (input && inputE164) {
    // remove any previous listener to avoid duplicates
    input.oninput = null;

    input.addEventListener('input', () => {
      const raw       = input.value;
      const cursorPos = input.selectionStart;

      const digitsBeforeCursor = raw.slice(0, cursorPos).replace(/\D/g, '').length;

      const formatter = new libphonenumber.AsYouType(region);
      const formatted = formatter.input(raw);

      try {
        const e164 = libphonenumber.parsePhoneNumber(raw, region).number;
        inputE164.value = e164;
      } catch { /* ignore partial inputs */ }

      // restore cursor position
      let newCursor = 0, digitCount = 0;
      while (newCursor < formatted.length && digitCount < digitsBeforeCursor) {
        if (/\d/.test(formatted[newCursor])) digitCount++;
        newCursor++;
      }
      input.value = formatted;
      input.setSelectionRange(newCursor, newCursor);
    });
  }

  // format static numbers
  document.querySelectorAll('.phone-number-static').forEach(el => {
    try {
      const pn = libphonenumber.parsePhoneNumber(el.textContent.trim(), region);
      el.textContent = pn.formatNational();
    } catch {}
  });
}

document.addEventListener('DOMContentLoaded', initPhoneField);

document.addEventListener('htmx:afterSwap', (e) => {
  // only re-initialize when the swap affects the phone block
  if (e.detail.target.closest('div.space-y-4')) {
    initPhoneField();
  }
});

phoneInE164 = function() {
  const region = (navigator.language || 'en-US').split('-')[1] || 'US';
  const phoneNumber = document.querySelector('input[name="phone_number"]').value.trim();

  if (!phoneNumber) {
    console.error('Phone number input is empty');
    return null;
  }

  try {
    const pn = libphonenumber.parsePhoneNumber(phoneNumber, region);
    return pn.number; // E.164 format
  } catch (err) {
    console.error('Invalid phone number:', phoneNumber, err);
    return null;
  }
};

resetConfirmButton = function() {
  const confirmButton = document.querySelector('#verify-code button[type="button"]');
  confirmButton.disabled = false;
  confirmButton.innerHTML = 'Confirm';
  confirmButton.classList.remove('opacity-50', 'cursor-not-allowed');
};