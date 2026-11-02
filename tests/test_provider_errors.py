"""Error-parser unit tests; not live API calls or simulated successful processing."""
import httpx
import pytest
from app.provider import response_error


def response(code=None,kind=None,status=429,headers=None,message='upstream private message'):
    return httpx.Response(status,json={'error':{'code':code,'type':kind,'message':message}},headers=headers)


@pytest.mark.parametrize('code',['insufficient_quota','billing_hard_limit_reached','billing_not_active'])
def test_quota_is_not_reported_as_temporary_throttle(code):
    error=response_error(response(code,'insufficient_quota'),'/chat/completions','gpt-4.1')
    assert error.details['category']=='quota'
    assert 'ChatGPT' in str(error)
    assert '等待重试通常不能解决' in str(error)


def test_rate_limit_contains_safe_retry_and_model_diagnostics():
    error=response_error(response('rate_limit_exceeded','tokens',headers={
        'retry-after':'12','x-request-id':'req_0123456789abcdef',
        'x-ratelimit-limit-tokens':'30000','x-ratelimit-remaining-tokens':'0',
        'x-ratelimit-reset-tokens':'1m2.5s'}),'/images/edits','gpt-image-1')
    assert error.details['category']=='rate_limit'
    assert error.details['retry_after_seconds']==12
    assert error.details['rate_limits']['remaining-tokens']=='0'
    assert error.details['model']=='gpt-image-1'
    assert '余额' in str(error) and '12' in str(error)


def test_unknown_429_does_not_guess_credit_exhaustion():
    error=response_error(httpx.Response(429,text='non-json body'),'/chat/completions')
    assert error.details['category']=='unknown_429'
    assert '暂不能区分' in str(error)


def test_no_secret_or_private_body_is_exposed():
    secret='sk-proj-private-diagnostic-test-1234567890'
    error=response_error(response(secret,secret,headers={
        'x-request-id':secret,'retry-after':secret,'x-ratelimit-reset-tokens':secret,
        'x-ratelimit-limit-requests':secret},message=secret),'/invalid/'+secret,secret)
    assert secret not in str(error)
    assert secret not in repr(error.details)
    assert error.details['code']=='unknown'
    assert error.details['endpoint']=='unknown'


def test_array_error_body_is_handled():
    error=response_error(httpx.Response(429,json={'error':['bad']}),'/images/edits')
    assert error.details['category']=='unknown_429'
