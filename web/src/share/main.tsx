// The share site served by `dataforge share serve`, which reads the API.
import { apiSource } from "./api-source";
import { mount } from "./mount";

mount(apiSource);
