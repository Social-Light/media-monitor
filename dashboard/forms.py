from django import forms

from discovery.models import SeedSource


class SeedSourceForm(forms.ModelForm):
    class Meta:
        model = SeedSource
        fields = [
            "name", "url", "source_type", "is_active",
            "crawl_interval", "keyword_filter", "use_playwright",
        ]
        widgets = {
            "name":           forms.TextInput(attrs={"class": "form-control"}),
            "url":            forms.URLInput(attrs={"class": "form-control"}),
            "source_type":    forms.Select(attrs={"class": "form-select"}),
            "is_active":      forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "crawl_interval": forms.NumberInput(attrs={"class": "form-control"}),
            "keyword_filter": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "use_playwright": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
