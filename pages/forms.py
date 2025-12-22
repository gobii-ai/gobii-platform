from django import forms


class MarketingContactForm(forms.Form):
    SOURCE_CHOICES = (
        ("healthcare_landing_page", "Healthcare landing page"),
        ("defense_landing_page", "Defense landing page"),
    )
    INQUIRY_CHOICES = (
        ("", "I am a..."),
        ("agency", "Defense agency looking for integration partners"),
        ("contractor", "Defense contractor interested in partnership"),
        ("other", "Other"),
    )

    source = forms.ChoiceField(choices=SOURCE_CHOICES)
    email = forms.EmailField(max_length=254)
    organization = forms.CharField(max_length=200, required=False)
    inquiry_type = forms.ChoiceField(choices=INQUIRY_CHOICES, required=False)
    message = forms.CharField(required=False, widget=forms.Textarea)
