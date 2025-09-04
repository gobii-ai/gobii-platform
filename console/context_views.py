import json
from django.http import JsonResponse
from django.views import View
from django.contrib.auth.mixins import LoginRequiredMixin
from api.models import OrganizationMembership


class SwitchContextView(LoginRequiredMixin, View):
    """Handle switching between personal and organization contexts."""
    
    def post(self, request):
        """Save the selected context to session."""
        try:
            data = json.loads(request.body)
            context_type = data.get('type')
            context_id = data.get('id')
            context_name = data.get('name')
            
            # Validate context type
            if context_type not in ['personal', 'organization']:
                return JsonResponse({'error': 'Invalid context type'}, status=400)
            
            # If personal context, validate it's the current user
            if context_type == 'personal':
                if str(request.user.id) != context_id:
                    return JsonResponse({'error': 'Invalid personal context'}, status=403)
                
                # Store in session
                request.session['context_type'] = 'personal'
                request.session['context_id'] = str(request.user.id)
                request.session['context_name'] = request.user.get_full_name() or request.user.username
                
            # If organization context, validate membership
            elif context_type == 'organization':
                try:
                    membership = OrganizationMembership.objects.get(
                        user=request.user,
                        org_id=context_id,
                        status=OrganizationMembership.OrgStatus.ACTIVE
                    )
                    
                    # Store in session
                    request.session['context_type'] = 'organization'
                    request.session['context_id'] = str(membership.org.id)
                    request.session['context_name'] = membership.org.name
                    
                except OrganizationMembership.DoesNotExist:
                    return JsonResponse({'error': 'Invalid organization context'}, status=403)
            
            return JsonResponse({
                'success': True,
                'context': {
                    'type': request.session['context_type'],
                    'id': request.session['context_id'],
                    'name': request.session['context_name']
                }
            })
            
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)