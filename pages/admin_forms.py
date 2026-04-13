from django import forms

from pages.models import CallToAction


class CallToActionAdminForm(forms.ModelForm):
    new_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="On create, this becomes the initial active text. On edit, this appends a new version.",
        label="New text",
    )

    class Meta:
        model = CallToAction
        fields = ("slug", "description")

    def clean_new_text(self):
        return (self.cleaned_data.get("new_text") or "").strip()

    def clean(self):
        cleaned_data = super().clean()
        new_text = cleaned_data.get("new_text") or ""

        if not self.instance.pk and not new_text:
            self.add_error("new_text", "Initial text is required when creating a CTA.")

        if self.instance.pk and new_text and new_text == self.instance.current_text:
            self.add_error("new_text", "New text matches the current active text.")

        return cleaned_data
