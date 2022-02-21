from django import forms

import vesper.django.app.form_utils as form_utils


_FORM_TITLE = 'Export clip tag counts to CSV file'


class ExportClipTagCountsToCsvFileForm(forms.Form):
    

    output_file_path = forms.CharField(
        label='Output file', max_length=255,
        widget=forms.TextInput(attrs={'class': 'command-form-wide-input'}))
