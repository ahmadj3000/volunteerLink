import { Amplify } from 'aws-amplify'

Amplify.configure({
  Auth: {
    Cognito: {
      userPoolId: 'us-east-1_is9qkiZoh',
      userPoolClientId: '6blnjutulha6k2gr0d1lf5fe9f',
      loginWith: {
        email: true,
      },
    },
  },
})
