from django.test import TestCase, Client, tag
from django.urls import reverse

@tag('eval_sim')
class WeatherSimViewTest(TestCase):
    def setUp(self):
        self.client = Client()

    def test_weather_sim_view_no_location(self):
        """
        Test that the view renders correctly without a location provided.
        """
        # Now expecting 200 OK because /eval/ is whitelisted in middleware
        response = self.client.get(reverse('evals:sim_weather'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SimWeather")
        self.assertContains(response, "Find your local weather")
        self.assertNotContains(response, "Wind: N 5 mph")

    def test_weather_sim_view_london(self):
        """
        Test weather information for London.
        """
        response = self.client.get(reverse('evals:sim_weather') + '?location=London')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "London")
        # Check values independently to avoid whitespace issues with HTML tags
        self.assertContains(response, "15°")
        self.assertContains(response, "Rainy")
        self.assertContains(response, "82%")
        self.assertContains(response, "Low (15)")

    def test_weather_sim_view_beijing(self):
        """
        Test weather information for Beijing.
        """
        response = self.client.get(reverse('evals:sim_weather') + '?location=Beijing')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Beijing")
        self.assertContains(response, "22°")
        self.assertContains(response, "Sunny")
        self.assertContains(response, "45%")
        self.assertContains(response, "High (150)")

    def test_weather_sim_view_san_francisco(self):
        """
        Test weather information for San Francisco.
        """
        response = self.client.get(reverse('evals:sim_weather') + '?location=San Francisco')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "San Francisco")
        self.assertContains(response, "18°")
        self.assertContains(response, "Foggy")
        self.assertContains(response, "75%")
        self.assertContains(response, "Moderate (45)")

    def test_weather_sim_view_washington_dc_variations(self):
        """
        Test deterministic responses for Washington DC variations.
        """
        variations = ["washington dc", "washington, d.c.", "DC", "Washington DC"]
        
        for location in variations:
            with self.subTest(location=location):
                response = self.client.get(reverse('evals:sim_weather') + f'?location={location}')
                self.assertEqual(response.status_code, 200)
                # We just check the deterministic temperature and condition to verify it hit the right block
                self.assertContains(response, "24°")
                self.assertContains(response, "Partly Cloudy")
                self.assertContains(response, "Moderate (55)")

    def test_weather_sim_view_unknown_location(self):
        """
        Test weather information for an unknown location, verifying deterministic fallback.
        """
        location = "Atlantis"
        response = self.client.get(reverse('evals:sim_weather') + f'?location={location}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, location)
        # Check for presence of general weather info structure
        self.assertContains(response, "Humidity")
        self.assertContains(response, "Air Quality")
