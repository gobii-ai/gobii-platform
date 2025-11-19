from django.views.generic import TemplateView
import hashlib

class WeatherSimView(TemplateView):
    template_name = "evals/sim/weather.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        location = self.request.GET.get('location', '').strip()
        
        if location:
            context['location'] = location
            
            # Deterministic generation based on location string
            # ensuring specific test cases exist
            
            loc_lower = location.lower()
            
            if "london" in loc_lower:
                context['temperature'] = 15
                context['condition'] = "Rainy"
                context['humidity'] = 82
                context['pollution_index'] = "Low (15)"
                context['pollution_class'] = "pollution-low"
            elif "beijing" in loc_lower:
                context['temperature'] = 22
                context['condition'] = "Sunny"
                context['humidity'] = 45
                context['pollution_index'] = "High (150)"
                context['pollution_class'] = "pollution-high"
            elif "san francisco" in loc_lower:
                context['temperature'] = 18
                context['condition'] = "Foggy"
                context['humidity'] = 75
                context['pollution_index'] = "Moderate (45)"
                context['pollution_class'] = "pollution-low"
            elif any(x in loc_lower for x in ["washington dc", "washington d.c.", "washington, dc", "washington, d.c."]) or loc_lower == "dc":
                context['temperature'] = 24
                context['condition'] = "Partly Cloudy"
                context['humidity'] = 60
                context['pollution_index'] = "Moderate (55)"
                context['pollution_class'] = "pollution-moderate"
            else:
                # Fallback deterministic "random"
                h = int(hashlib.md5(loc_lower.encode()).hexdigest(), 16)
                context['temperature'] = 10 + (h % 25)
                conditions = ["Sunny", "Cloudy", "Rainy", "Stormy", "Snowy"]
                context['condition'] = conditions[h % len(conditions)]
                context['humidity'] = 30 + (h % 70)
                
                p_val = h % 200
                if p_val > 100:
                    context['pollution_index'] = f"High ({p_val})"
                    context['pollution_class'] = "pollution-high"
                else:
                    context['pollution_index'] = f"Low ({p_val})"
                    context['pollution_class'] = "pollution-low"
                    
        return context
