from django import template

register = template.Library()

@register.filter(name='add_class')
def add_class(field, css):
    """Return field rendered with the given CSS classes appended."""
    existing = field.field.widget.attrs.get('class', '')
    combined = f"{existing} {css}".strip()
    return field.as_widget(attrs={**field.field.widget.attrs, 'class': combined}) 