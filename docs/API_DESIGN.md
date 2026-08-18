# API Design

> **Superseded** by [`05-api-design.md`](./05-api-design.md), which has the
> concrete route list. Kept for reference — the REST-maturity principles
> and error-format shape below still apply.

## Principles


API should follow REST maturity principles as much as practical.


Requirements:

- Resources instead of actions
- Correct HTTP verbs
- Stateless requests
- Consistent responses
- Proper status codes
- Pagination
- Filtering
- Sorting


## Versioning


All APIs should be versioned.


Example:

/api/v1/media-files



## Resources


Providers:


GET /api/v1/providers

POST /api/v1/providers

GET /api/v1/providers/{id}

DELETE /api/v1/providers/{id}



Media Files:


GET /api/v1/media-files

GET /api/v1/media-files/{id}

POST /api/v1/media-files

DELETE /api/v1/media-files/{id}



Sharing:


POST /api/v1/media-files/{id}/shares

GET /api/v1/shares/{token}



Search:


GET /api/v1/search?q=query



## Error Format


All errors should have consistent structure:


{
  "code": "error_code",
  "message": "Human readable message",
  "details": {}
}


