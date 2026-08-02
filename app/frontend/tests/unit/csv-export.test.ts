import { describe, it, expect } from "vitest";
import { convertToCsv } from "@/lib/csv-export";

describe("convertToCsv", () => {
  it("converts simple data to CSV", () => {
    const columns = ["name", "age"];
    const data = [
      { name: "Alice", age: 30 },
      { name: "Bob", age: 25 },
    ];
    const result = convertToCsv(columns, data);
    expect(result).toBe("name,age\nAlice,30\nBob,25");
  });

  it("returns empty string for empty data", () => {
    expect(convertToCsv([], [])).toBe("");
    expect(convertToCsv(["name"], [])).toBe("");
    expect(convertToCsv([], [{ name: "test" }])).toBe("");
  });

  it("escapes values with commas", () => {
    const columns = ["name", "address"];
    const data = [{ name: "Alice", address: "New York, USA" }];
    const result = convertToCsv(columns, data);
    expect(result).toBe('name,address\nAlice,"New York, USA"');
  });

  it("escapes values with quotes", () => {
    const columns = ["name", "quote"];
    const data = [{ name: "Alice", quote: 'She said "hello"' }];
    const result = convertToCsv(columns, data);
    expect(result).toBe('name,quote\nAlice,"She said ""hello"""');
  });

  it("escapes values with newlines", () => {
    const columns = ["name", "record"];
    const data = [{ name: "Alice", record: "Line 1\nLine 2" }];
    const result = convertToCsv(columns, data);
    expect(result).toBe('name,record\nAlice,"Line 1\nLine 2"');
  });

  it("handles null and undefined values", () => {
    const columns = ["name", "value"];
    const data = [
      { name: "null", value: null },
      { name: "undefined", value: undefined },
    ];
    const result = convertToCsv(columns, data);
    expect(result).toBe("name,value\nnull,\nundefined,");
  });

  it("handles numeric values", () => {
    const columns = ["id", "price", "quantity"];
    const data = [{ id: 1, price: 19.99, quantity: 100 }];
    const result = convertToCsv(columns, data);
    expect(result).toBe("id,price,quantity\n1,19.99,100");
  });

  it("handles boolean values", () => {
    const columns = ["name", "active"];
    const data = [
      { name: "Alice", active: true },
      { name: "Bob", active: false },
    ];
    const result = convertToCsv(columns, data);
    expect(result).toBe("name,active\nAlice,true\nBob,false");
  });
});
