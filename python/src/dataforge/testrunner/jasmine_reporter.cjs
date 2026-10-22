// A Jasmine reporter that writes each spec's outcome as JSON to the file
// TESTRUNNER_JASMINE_REPORT names: {specs: [{titles, status, error,
// duration, message, trace}], errors: [<message>]}. An error of a suite, such
// as one in its afterAll, is a spec named as the suite; one of the whole run,
// such as one in loading the file, is in errors.
"use strict";

const fs = require("fs");

module.exports = class TestrunnerReporter {
  constructor() {
    this.suites = new Map();
    this.open = [];
    this.specs = [];
  }

  // Jasmine before 4 gives no parentSuiteId; its suites start and end in
  // order then.
  parent(result) {
    if (result.parentSuiteId !== undefined) {
      return result.parentSuiteId;
    }
    return this.open[this.open.length - 1];
  }

  titles(id) {
    const names = [];
    for (let at = id; at && this.suites.has(at); ) {
      const suite = this.suites.get(at);
      names.unshift(suite.description);
      at = suite.parent;
    }
    return names;
  }

  suiteStarted(result) {
    const parent = this.parent(result);
    this.suites.set(result.id, { description: result.description, parent });
    this.open.push(result.id);
  }

  suiteDone(result) {
    this.open.pop();
    if (result.failedExpectations && result.failedExpectations.length) {
      this.add(result, this.titles(result.id), "failed", true);
    }
  }

  specDone(result) {
    const titles = [...this.titles(this.parent(result)), result.description];
    this.add(result, titles, result.status, false);
  }

  add(result, titles, status, error) {
    const failed = result.failedExpectations || [];
    this.specs.push({
      titles,
      status,
      error,
      duration: result.duration || 0,
      message: failed.length ? failed[0].message : null,
      trace: failed.map((f) => f.stack || f.message).join("\n\n") || null,
    });
  }

  jasmineDone(result) {
    const failed = (result && result.failedExpectations) || [];
    const report = { specs: this.specs, errors: failed.map((f) => f.message) };
    fs.writeFileSync(process.env.TESTRUNNER_JASMINE_REPORT, JSON.stringify(report));
  }
};
