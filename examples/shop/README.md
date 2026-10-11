# Demo shop

Login checks a demo session token with `validate_token` in `auth.py`.
`SessionService.authorize` shares the same validator.
The example illustrates source navigation, not production authentication.

Try:

```sh
balecontext index --root examples/shop
balecontext query "validate session token" --root examples/shop
balecontext related validate_token --direction callers --root examples/shop
balecontext export --root examples/shop
```
