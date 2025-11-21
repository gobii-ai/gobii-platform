from drf_spectacular.extensions import OpenApiAuthenticationExtension

class APIKeyAuthScheme(OpenApiAuthenticationExtension):
    target_class = 'api.auth.APIKeyAuthentication'
    name = 'ApiKeyAuth'

    def get_security_definition(self, auto_schema):
        """
        Retrieves the security definition for API authentication requirements.

        This function provides the security scheme definition as a dictionary,
        which specifies the type of authentication used for securing API
        requests. The schema defined here employs an API key provided via
        request headers.

        Parameters:
            auto_schema: Not used within the method. Retained for potential future
            extension or interface conformance purposes.

        Returns:
            dict: A dictionary containing the security scheme configuration,
            including the type, location, and header name for the API key.
        """
        return {
            'type': 'apiKey',
            'in': 'header',
            'name': 'X-Api-Key',  # matches APIKeyAuthentication.keyword
        }