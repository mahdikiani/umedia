from fastapi import Response


class S3Error(Exception):
    code = "InternalError"
    message = "We encountered an internal error. Please try again."
    status_code = 500

    def __init__(self, message: str | None = None) -> None:
        if message is not None:
            self.message = message
        super().__init__(self.message)

    def to_response(self) -> Response:
        from .xml import error_xml

        return Response(
            content=error_xml(self.code, self.message),
            status_code=self.status_code,
            media_type="application/xml",
        )


class AccessDenied(S3Error):
    code = "AccessDenied"
    message = "Access Denied"
    status_code = 403


class InvalidAccessKeyId(S3Error):
    code = "InvalidAccessKeyId"
    message = "The AWS Access Key Id you provided does not exist in our records."
    status_code = 403


class SignatureDoesNotMatch(S3Error):
    code = "SignatureDoesNotMatch"
    message = (
        "The request signature we calculated does not match the signature you "
        "provided."
    )
    status_code = 403


class NoSuchBucket(S3Error):
    code = "NoSuchBucket"
    message = "The specified bucket does not exist"
    status_code = 404


class NoSuchKey(S3Error):
    code = "NoSuchKey"
    message = "The specified key does not exist."
    status_code = 404


class NoSuchUpload(S3Error):
    code = "NoSuchUpload"
    message = "The specified multipart upload does not exist."
    status_code = 404


class InvalidArgument(S3Error):
    code = "InvalidArgument"
    message = "Invalid argument"
    status_code = 400
